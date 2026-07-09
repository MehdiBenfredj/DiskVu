# Plan: fix the three deferred issues from the code review

Scope: exactly the three items noted-but-not-changed in the July 2026 review of `main.go`.
No new features beyond completing these fixes. After each task: `go vet ./...`, `go build`,
and a `go build -race` run of the TUI (see "Verification" per task).

---

## Task 1 — Untrack the committed binaries (small)

**Problem:** `diskvu` and `main` are compiled binaries tracked in git. `.gitignore`
lists them, but gitignore does not affect already-tracked files, so every rebuild
shows them as modified (see `git status`).

**Steps:**
1. `git rm --cached diskvu main`
2. Clean up `.gitignore`: `diskvu` appears twice (lines 2 and 24) and `main` is at
   line 25 under "OS-generated", which is the wrong section. Keep a single
   "Built binaries" block at the top: `diskvu`, `diskvu-*`, `main`.
3. Commit (the working-tree files stay on disk, just untracked).

**Verification:** `git status` shows clean after a fresh `go build -o diskvu .`.

---

## Task 2 — Restore cursor/scroll position when going back (`goBack`)

**Problem:** `enterDir` pushes `navHistory{path, cursor, scroll}` onto `a.history`,
but `goBack` pops the entry and discards the saved cursor/scroll — the stored fields
are dead. After going back, the cursor always resets to row 0. The reset happens in
two places: `startScan` (clears immediately) and the main loop's `upd.done` handler
(`app.cursor = 0; app.scroll = 0`).

**Design:** a "pending restore" that survives the scan and is applied when the scan
finishes. Key ordering trick: `startScan` clears any pending restore, so `goBack`
must set it *after* calling `startScan`. All other navigation entry points
(`enterDir`, `r`, `~`, delete-triggered rescans) then get reset-to-top behavior for
free.

**Steps:**
1. Add a field to `App`: `pendingNav *navHistory` (nil = no restore).
2. In `startScan`, alongside the existing `a.cursor = 0; a.scroll = 0`, add
   `a.pendingNav = nil`.
3. In `goBack`, in the branch where `prev.path == parent` (history hit), after
   `a.startScan()` add `a.pendingNav = &prev`. Remove the stale comment there
   ("Restore position after scan finishes naturally is tricky…") — it no longer
   applies. The history-miss branch (`a.history = nil`) stays reset-to-top.
4. In the main loop's `upd.done` block (inside the `upd.id == app.scanID` guard),
   replace the unconditional reset with:

   ```go
   app.cursor, app.scroll = 0, 0
   if app.pendingNav != nil {
       app.cursor = clamp(app.pendingNav.cursor, 0, max(0, len(app.entries)-1))
       lh := app.listHeight()
       app.scroll = clamp(app.pendingNav.scroll, 0, max(0, len(app.entries)-lh))
       // keep cursor visible if the list shrank or the terminal was resized
       if app.cursor < app.scroll {
           app.scroll = app.cursor
       } else if app.cursor >= app.scroll+lh {
           app.scroll = app.cursor - lh + 1
       }
       app.pendingNav = nil
   }
   ```

   Clamping matters: entries may have been deleted since the position was saved,
   and the terminal may have been resized.

**Optional refinement (implementer's choice, still within scope):** instead of the
raw saved index, first look for the entry whose `Path` equals the directory just
returned from (the sort order can change between visits since sizes change), and
fall back to the clamped saved index if not found. This gives "cursor lands on the
folder I just left", which is what users actually expect.

**Verification:** build with `-race`; in a pty session (`script -q` works on macOS,
`TERM=xterm-256color`), navigate: `j j l` (enter third entry), wait for the scan,
`h` (back) — cursor must return to the third row, not row 0. Also test: enter a
dir, delete entries in the parent from another shell, go back — no crash, cursor
clamped.

---

## Task 3 — Cache staleness for changes inside subdirectories

**Problem:** `Cache.Get` validates by the directory's own mtime, which only changes
when a *direct* child is added/removed. Two consequences:
- After deleting something inside `/a/b`, the cached listing of `/a` still shows
  `b`'s old size (the app's own deletes create stale data it then serves).
- External changes deep in a tree are never detected until `r`.

Fix in three parts; parts (a) and (b) are the important ones.

### 3a. Invalidate ancestors after a successful delete

In `deleteSelected`, the success branch currently does
`a.cache.Invalidate(a.currentPath)`. Replace with invalidation of the deleted path
and every ancestor up to the root:

```go
for p := ent.Path; ; p = filepath.Dir(p) {
    a.cache.Invalidate(p)
    if p == filepath.Dir(p) { // reached "/"
        break
    }
}
```

This is cheap (a handful of map deletes) and fixes exactly the case the app itself
causes.

### 3b. Purge cached subtrees of a deleted directory

Prefetch may have cached listings *under* a directory that then gets deleted. Their
`Get` self-expires (stat fails), but the entries linger in memory forever. Add:

```go
func (c *Cache) InvalidateTree(path string) {
    c.mu.Lock()
    defer c.mu.Unlock()
    prefix := path + "/"
    for k := range c.ents {
        if k == path || strings.HasPrefix(k, prefix) {
            delete(c.ents, k)
            delete(c.mtimes, k)
        }
    }
}
```

Call it for `ent.Path` in the delete success branch when `ent.IsDir` (3a's loop
still handles the ancestors).

### 3c. TTL as a catch-all for external changes

Add `CacheTTL = 5 * time.Minute` to the constants block. Store the insertion time
and treat older entries as misses.

Recommended refactor while in there: collapse the parallel maps into one —
`map[string]cacheEntry` with `cacheEntry{ents []*DirEntry; mtime time.Time; added time.Time}` —
so `Get`/`Put`/`Invalidate`/`InvalidateTree` touch a single map. `Get` returns a
miss when `mt != stat.ModTime()` **or** `time.Since(added) > CacheTTL`.

`r` (manual rescan) remains the immediate-refresh escape hatch; prefetch simply
re-runs on expired entries — no other call sites need changes.

**Verification:** build with `-race`. In a pty session over a scratch tree
`root/a/b/blob.bin`: start at `root`, enter `a` (this caches `root` and `a`), from
another shell delete `blob.bin` via the app is not possible — instead use the app:
navigate into `b`, delete `blob.bin` with `d`+`y`, then go back to `a` and `root`
and confirm both show the reduced size *without* pressing `r` (pre-fix they show
the stale cached size). For the TTL, temporarily set `CacheTTL = 2 * time.Second`
in a manual test run, touch a file deep in the tree from another shell, revisit
after 3 s and confirm a rescan happens; restore the 5-minute value before
committing.

---

## Ordering & commits

Tasks are independent; do them as three separate commits in the order above
(Task 1 first so later `git status` output is clean). Do not tag a release —
that pipeline is GoReleaser-driven and out of scope.
