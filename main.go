package main

import (
	"bytes"
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"os/user"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gdamore/tcell/v2"
	"github.com/mattn/go-runewidth"
)

// ═══════════════════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════════════════

const (
	Version       = "2.0.0"
	UnknownSize   = int64(-1)
	PendingSize   = int64(-2)
	DuTimeout     = 180 * time.Second
	MaxScanDepth  = 64
	MaxWorkers    = 16
	TickMs        = 100
	MsgTTL        = 2500 * time.Millisecond
	MaxPartialCap = 50
	PrefetchMinMB = 300
	BarWidth      = 20
)

// ═══════════════════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════════════════

// DirEntry represents a file or directory with its computed size.
type DirEntry struct {
	Name      string
	Path      string
	IsDir     bool
	Size      int64
	ItemCount int
	Error     string
}

// ScanEvent is sent during a scan to stream results and progress.
type ScanEvent struct {
	Entry    *DirEntry
	Message  string // e.g. "Sizing Downloads..."
	DirTotal int    // set once after initial listing
	DirDone  bool   // indicates a directory finished sizing
}

// scanUpdate carries incremental UI state from the scan goroutine to the main loop.
// All App field mutations happen only on the main goroutine; the scan goroutine
// communicates exclusively through this struct.
type scanUpdate struct {
	entries   []*DirEntry
	total     int64
	status    string
	dirsDone  int
	dirsTotal int
	done      bool
	scanTime  time.Duration
}

type navHistory struct {
	path   string
	cursor int
	scroll int
}

// ═══════════════════════════════════════════════════════════════════════════════
// Utility Functions
// ═══════════════════════════════════════════════════════════════════════════════

func humanSize(n int64) string {
	if n == PendingSize {
		return "  ...   "
	}
	if n == UnknownSize {
		return "   ???  "
	}
	if n < 0 {
		return "ERR"
	}
	units := []string{"B", "K", "M", "G", "T", "P"}
	abs := float64(n)
	if abs < 0 {
		abs = -abs
	}
	for _, unit := range units {
		if abs < 1024 {
			if unit == "B" {
				return fmt.Sprintf("%4d %s", n, unit)
			}
			return fmt.Sprintf("%6.1f %s", abs, unit)
		}
		abs /= 1024
	}
	return fmt.Sprintf("%6.1f E", abs)
}

var sizeThresholds = []struct {
	bytes int64
	emoji string
}{
	{1 << 40, "🐋"}, {100 << 30, "🦕"}, {10 << 30, "🐘"}, {1 << 30, "🦁"},
	{100 << 20, "🐻"}, {10 << 20, "🦊"}, {1 << 20, "🐦"}, {0, "🐜"},
}

func sizeEmoji(n int64) string {
	if n == PendingSize {
		return "⏳"
	}
	if n == UnknownSize {
		return "🔒"
	}
	for _, t := range sizeThresholds {
		if n >= t.bytes {
			return t.emoji
		}
	}
	return "🐜"
}

var extIcons = map[string]string{
	".zip": "📦", ".tar": "📦", ".gz": "📦", ".bz2": "📦",
	".xz": "📦", ".rar": "📦", ".7z": "📦", ".tgz": "📦",
	".mp4": "🎬", ".mov": "🎬", ".avi": "🎬", ".mkv": "🎬",
	".wmv": "🎬", ".webm": "🎬", ".m4v": "🎬",
	".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".aac": "🎵",
	".ogg": "🎵", ".m4a": "🎵",
	".jpg": "🖼️ ", ".jpeg": "🖼️ ", ".png": "🖼️ ", ".gif": "🖼️ ",
	".svg": "🖼️ ", ".webp": "🖼️ ", ".heic": "🖼️ ", ".bmp": "🖼️ ",
	".pdf": "📄", ".doc": "📝", ".docx": "📝", ".txt": "📝",
	".md": "📝", ".pages": "📝", ".odt": "📝",
	".xls": "📊", ".xlsx": "📊", ".csv": "📊", ".numbers": "📊",
	".dmg": "💿", ".iso": "💿", ".pkg": "🍺", ".app": "🖥️ ",
	".py": "🐍", ".js": "🟨", ".ts": "🟦", ".go": "🐹",
	".rs": "🦀", ".c": "⚙️ ", ".cpp": "⚙️ ", ".h": "⚙️ ",
	".java": "☕", ".rb": "💎", ".sh": "🐚", ".bash": "🐚",
	".db": "🗄️ ", ".sqlite": "🗄️ ", ".json": "🗂️ ", ".xml": "🗂️ ",
	".yaml": "🗂️ ", ".yml": "🗂️ ",
	".ttf": "🔤", ".otf": "🔤", ".woff": "🔤",
	".DS_Store": "🗑️ ",
}

func fileIcon(name string, isDir bool) string {
	if isDir {
		return "📁"
	}
	ext := strings.ToLower(filepath.Ext(name))
	if icon, ok := extIcons[name]; ok {
		return icon
	}
	if icon, ok := extIcons[ext]; ok {
		return icon
	}
	return "📄"
}

var scanQuips = []string{
	"🔍 Hunting for byte monsters...",
	"🕵️  Investigating your digital hoard...",
	"🐘 Checking for elephants in the room...",
	"🧹 Assessing the chaos...",
	"💾 Counting every single bit...",
	"🏋️  Lifting heavy directories...",
	"🤿 Deep-diving into your filesystem...",
	"🦔 Carefully scanning, do not disturb...",
	"🚀 Going fast, hold tight...",
	"📦 Unpacking the truth...",
	"🐢 Your disk is large... or is it just slow? 👀",
	"🌊 Surfing the inode wave...",
}

// drawString writes a string to the screen starting at (x, y), returning the
// final x position. Handles wide runes via go-runewidth.
func drawString(s tcell.Screen, x, y int, str string, style tcell.Style) int {
	for _, r := range str {
		if r == 0xFE0F || r == 0x200D { // Skip variation selectors
			continue
		}
		s.SetContent(x, y, r, nil, style)
		w := runewidth.RuneWidth(r)
		if w > 0 {
			x += w
		}
	}
	return x
}

func fillLine(s tcell.Screen, y, w int, style tcell.Style) {
	for x := 0; x < w; x++ {
		s.SetContent(x, y, ' ', nil, style)
	}
}

func truncate(s string, maxRunes int) string {
	runes := []rune(s)
	if len(runes) <= maxRunes {
		return s
	}
	return string(runes[:maxRunes])
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skip Logic & Mount Table
// ═══════════════════════════════════════════════════════════════════════════════

var skipDirNames = map[string]bool{
	"proc": true, "sys": true, "dev": true, "run": true, "snap": true,
	"debug": true, "tracing": true, "net": true, "home": true,
	"cgroup": true, "cgroup2": true, "cgroupv2": true,
	"configfs": true, "securityfs": true, "pstore": true, "efivarfs": true,
}

var skipAbsPaths map[string]bool

func init() {
	if runtime.GOOS == "darwin" {
		skipAbsPaths = map[string]bool{
			"/private/var/vm": true, "/private/var/folders": true, "/cores": true,
		}
	} else {
		skipAbsPaths = map[string]bool{
			"/proc": true, "/sys": true, "/dev": true, "/run/user": true,
			"/sys/kernel/debug": true, "/sys/kernel/tracing": true,
		}
	}
}

var networkFSTypes = map[string]bool{
	"nfs": true, "nfs4": true, "nfs3": true, "cifs": true, "smb": true,
	"smbfs": true, "afs": true, "coda": true, "ncpfs": true, "ncp": true,
	"davfs": true, "sshfs": true, "ftpfs": true, "s3fs": true, "efs": true,
}

var mountTable map[string]string
var mountOnce sync.Once

func buildMountTable() {
	mountTable = make(map[string]string)
	if runtime.GOOS == "linux" {
		data, err := os.ReadFile("/proc/mounts")
		if err != nil {
			return
		}
		for _, line := range strings.Split(string(data), "\n") {
			parts := strings.Fields(line)
			if len(parts) >= 3 {
				mountTable[parts[1]] = strings.ToLower(parts[2])
			}
		}
	} else if runtime.GOOS == "darwin" {
		out, err := exec.Command("mount").Output()
		if err != nil {
			return
		}
		re := regexp.MustCompile(`on (.+) \((\w+)`)
		for _, line := range strings.Split(string(out), "\n") {
			m := re.FindStringSubmatch(line)
			if len(m) == 3 {
				mountTable[m[1]] = strings.ToLower(m[2])
			}
		}
	}
}

func isNetworkFS(path string) bool {
	mountOnce.Do(buildMountTable)
	var best string
	var fsType string
	norm := filepath.Clean(path)
	for m, t := range mountTable {
		if norm == m || strings.HasPrefix(norm, m+"/") {
			if len(m) > len(best) {
				best = m
				fsType = t
			}
		}
	}
	return networkFSTypes[fsType]
}

func shouldSkip(path string, skipNet bool) bool {
	norm := filepath.Clean(path)
	if skipAbsPaths[norm] {
		return true
	}
	for prefix := range skipAbsPaths {
		if strings.HasPrefix(norm, prefix+"/") {
			return true
		}
	}
	if skipDirNames[filepath.Base(norm)] {
		return true
	}
	if skipNet && isNetworkFS(path) {
		return true
	}
	return false
}

var protectedPaths map[string]bool

func init() {
	protectedPaths = map[string]bool{
		"/": true, "/etc": true, "/usr": true, "/bin": true, "/sbin": true,
		"/lib": true, "/lib64": true, "/boot": true, "/sys": true,
		"/proc": true, "/dev": true, "/run": true, "/var": true,
		"/System": true, "/Library": true, "/Applications": true,
	}
	if u, err := user.Current(); err == nil {
		protectedPaths[u.HomeDir] = true
	}
}

func accessHint() string {
	if runtime.GOOS == "darwin" {
		return "grant Full Disk Access to Terminal in System Settings › Privacy & Security"
	}
	return "run as root (sudo diskvu /) or fix directory permissions"
}

// ═══════════════════════════════════════════════════════════════════════════════
// Scanner
// ═══════════════════════════════════════════════════════════════════════════════

// Cache stores directory scan results.
type Cache struct {
	mu    sync.RWMutex
	ents  map[string][]*DirEntry
	mtimes map[string]time.Time
}

func NewCache() *Cache {
	return &Cache{ents: make(map[string][]*DirEntry), mtimes: make(map[string]time.Time)}
}

func (c *Cache) Get(path string) ([]*DirEntry, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	stat, err := os.Stat(path)
	if err != nil {
		return nil, false
	}
	if mt, ok := c.mtimes[path]; ok && mt == stat.ModTime() {
		ents, okE := c.ents[path]
		return ents, okE
	}
	return nil, false
}

func (c *Cache) Put(path string, entries []*DirEntry) {
	c.mu.Lock()
	defer c.mu.Unlock()
	stat, err := os.Stat(path)
	if err != nil {
		return
	}
	c.ents[path] = entries
	c.mtimes[path] = stat.ModTime()
}

func (c *Cache) Invalidate(path string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.ents, path)
	delete(c.mtimes, path)
}

// computeDirSize is the pure-Go recursive fallback if `du` fails.
func computeDirSize(ctx context.Context, path string, depth int) (int64, int) {
	if depth > MaxScanDepth || shouldSkip(path, false) {
		return 0, 0
	}
	var total int64
	var count int
	entries, err := os.ReadDir(path)
	if err != nil {
		return 0, 0
	}
	for _, e := range entries {
		select {
		case <-ctx.Done():
			return 0, 0
		default:
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		count++
		if e.IsDir() {
			s, c := computeDirSize(ctx, filepath.Join(path, e.Name()), depth+1)
			total += s
			count += c
		} else {
			total += info.Size()
		}
	}
	return total, count
}

func shallowCount(path string) int {
	entries, err := os.ReadDir(path)
	if err != nil {
		return 0
	}
	return len(entries)
}

// workerSizeDir calculates the size of a directory using `du`, falling back to Go.
func workerSizeDir(ctx context.Context, path string, skipNet bool) (int64, int, string) {
	if shouldSkip(path, skipNet) {
		return 0, 0, "skipped"
	}

	isNet := isNetworkFS(path)
	errMsg := ""
	if isNet {
		errMsg = "network"
	}

	// Fast path: use du
	ctxDu, cancel := context.WithTimeout(ctx, DuTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctxDu, "du", "-s", "-k", path)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	stdoutStr := stdout.String()
	stderrLower := strings.ToLower(stderr.String())

	accessDenied := strings.Contains(stderrLower, "permission denied") ||
		strings.Contains(stderrLower, "operation not permitted")

	if err != nil && !accessDenied {
		// du failed entirely (e.g. not found), fallback to Go
		goto fallback
	}

	// Parse du output
	for _, line := range strings.Split(stdoutStr, "\n") {
		parts := strings.SplitN(line, "\t", 2)
		if len(parts) == 2 {
			kb, errA := strconv.ParseInt(parts[0], 10, 64)
			if errA == nil {
				size := kb * 1024
				if size == 0 && accessDenied {
					return UnknownSize, 0, "inaccessible"
				}
				return size, shallowCount(path), errMsg
			}
		}
	}
	if accessDenied {
		return UnknownSize, 0, "inaccessible"
	}

fallback:
	// Check basic permission before expensive walk
	f, err := os.Open(path)
	if err != nil {
		if os.IsPermission(err) {
			return UnknownSize, 0, "inaccessible"
		}
	}
	f.Close()

	size, count := computeDirSize(ctx, path, 0)
	return size, count, errMsg
}

// scanDirectory performs the concurrent scan and streams results.
func scanDirectory(ctx context.Context, path string, skipNet bool, events chan<- ScanEvent, cache *Cache) ([]*DirEntry, error) {
	// Check cache first
	if cached, ok := cache.Get(path); ok {
		for _, e := range cached {
			events <- ScanEvent{Entry: e}
		}
		return cached, nil
	}

	entries, err := os.ReadDir(path)
	if err != nil {
		return nil, err
	}

	var dirItems []os.DirEntry
	var results []*DirEntry

	// Phase 1: Emit files and pending directories
	for _, e := range entries {
		info, err := e.Info()
		if err != nil {
			results = append(results, &DirEntry{Name: e.Name(), Path: filepath.Join(path, e.Name()), Error: err.Error()})
			continue
		}

		isSymlink := info.Mode()&os.ModeSymlink != 0

		if isSymlink {
			ent := &DirEntry{Name: e.Name(), Path: filepath.Join(path, e.Name()), Size: info.Size(), Error: "symlink"}
			results = append(results, ent)
			events <- ScanEvent{Entry: ent}
		} else if e.IsDir() {
			p := filepath.Join(path, e.Name())
			if shouldSkip(p, skipNet) {
				ent := &DirEntry{Name: e.Name(), Path: p, IsDir: true, Error: "skipped"}
				results = append(results, ent)
				events <- ScanEvent{Entry: ent}
			} else {
				ent := &DirEntry{Name: e.Name(), Path: p, IsDir: true, Size: PendingSize}
				results = append(results, ent)
				events <- ScanEvent{Entry: ent}
				dirItems = append(dirItems, e)
			}
		} else {
			ent := &DirEntry{Name: e.Name(), Path: filepath.Join(path, e.Name()), Size: info.Size()}
			results = append(results, ent)
			events <- ScanEvent{Entry: ent}
		}
	}

	events <- ScanEvent{DirTotal: len(dirItems)}

	// Phase 2: Size directories in parallel
	var wg sync.WaitGroup
	workerCount := MaxWorkers
	if len(dirItems) < workerCount {
		workerCount = len(dirItems)
	}

	jobs := make(chan os.DirEntry, len(dirItems))
	var mu sync.Mutex

	for i := 0; i < workerCount; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for item := range jobs {
				select {
				case <-ctx.Done():
					return
				default:
				}

				p := filepath.Join(path, item.Name())
				base := item.Name()
				if !strings.HasPrefix(base, ".") {
					events <- ScanEvent{Message: base}
				}

				size, count, errStr := workerSizeDir(ctx, p, skipNet)

				ent := &DirEntry{
					Name: item.Name(), Path: p, IsDir: true,
					Size: size, ItemCount: count, Error: errStr,
				}

				mu.Lock()
				results = append(results, ent)
				mu.Unlock()

				events <- ScanEvent{Entry: ent, DirDone: true}
			}
		}()
	}

	for _, item := range dirItems {
		jobs <- item
	}
	close(jobs)
	wg.Wait()

	sort.Slice(results, func(i, j int) bool {
		return results[i].Size > results[j].Size
	})

	cache.Put(path, results)
	return results, nil
}

// ═══════════════════════════════════════════════════════════════════════════════
// Application / TUI
// ═══════════════════════════════════════════════════════════════════════════════

type App struct {
	screen  tcell.Screen
	cache   *Cache
	skipNet bool

	currentPath string
	entries     []*DirEntry
	cursor      int
	scroll      int
	totalSize   int64
	scanTime    time.Duration
	history     []navHistory

	scanning  bool
	scanID    int
	scanCancel context.CancelFunc

	message     string
	messageTime time.Time

	// Live progress tracking
	scanStatus   string
	scanDirsDone int
	scanDirsTotal int

	prefetchCancel context.CancelFunc

	// scanUpdates is the one-way channel from scan goroutines to the main loop.
	// The scan goroutine never writes App fields directly.
	scanUpdates chan scanUpdate
}

func NewApp(s tcell.Screen, path string, skipNet bool) *App {
	return &App{
		screen:      s,
		cache:       NewCache(),
		skipNet:     skipNet,
		currentPath: path,
		scanUpdates: make(chan scanUpdate, 8),
	}
}

func (a *App) listHeight() int {
	_, h := a.screen.Size()
	return h - 4
}

func (a *App) setMessage(msg string) {
	a.message = msg
	a.messageTime = time.Now()
}

func (a *App) startScan() {
	if a.scanCancel != nil {
		a.scanCancel()
	}
	// Drain any leftover updates from the previous scan.
	for len(a.scanUpdates) > 0 {
		<-a.scanUpdates
	}

	ctx, cancel := context.WithCancel(context.Background())
	a.scanCancel = cancel
	a.scanID++
	currentScanID := a.scanID

	a.scanning = true
	a.scanStatus = ""
	a.scanDirsDone = 0
	a.scanDirsTotal = 0
	// Clear stale entries immediately so we never show the previous
	// directory's content while the new scan is loading.
	a.entries = nil
	a.cursor = 0
	a.scroll = 0

	events := make(chan ScanEvent, 100)

	go func() {
		defer close(events)
		scanDirectory(ctx, a.currentPath, a.skipNet, events, a.cache)
	}()

	// Drain events in background. This goroutine NEVER writes App fields directly;
	// it only sends scanUpdate messages which the main loop applies safely.
	go func() {
		t0 := time.Now()
		seen := make(map[string]*DirEntry)
		lastSent := time.Now()

		var status string
		var dirsDone, dirsTotal int

		buildUpdate := func(done bool) scanUpdate {
			partial := make([]*DirEntry, 0, len(seen))
			for _, ent := range seen {
				partial = append(partial, ent)
			}
			sort.Slice(partial, func(i, j int) bool {
				pi, pj := partial[i].Size, partial[j].Size
				// Pending/unknown entries go to the bottom.
				if pi < 0 {
					pi = -1
				} else {
					pi = 0
				}
				if pj < 0 {
					pj = -1
				} else {
					pj = 0
				}
				return partial[i].Size+pi > partial[j].Size+pj
			})
			var total int64
			for _, e := range partial {
				if e.Size > 0 {
					total += e.Size
				}
			}
			return scanUpdate{
				entries:   partial,
				total:     total,
				status:    status,
				dirsDone:  dirsDone,
				dirsTotal: dirsTotal,
				done:      done,
				scanTime:  time.Since(t0),
			}
		}

		trySend := func(upd scanUpdate) {
			select {
			case a.scanUpdates <- upd:
			default: // main loop is busy; drop — it will catch up on the next tick
			}
		}

		for ev := range events {
			if ctx.Err() != nil || a.scanID != currentScanID {
				return
			}
			if ev.Entry != nil {
				seen[ev.Entry.Path] = ev.Entry
			}
			if ev.Message != "" {
				status = ev.Message
			}
			if ev.DirTotal > 0 {
				dirsTotal = ev.DirTotal
			}
			if ev.DirDone {
				dirsDone++
			}
			// Throttle: push a UI update at most every 100 ms to avoid saturating
			// the channel and doing needless work on huge directories.
			if time.Since(lastSent) >= 100*time.Millisecond {
				trySend(buildUpdate(false))
				lastSent = time.Now()
			}
		}

		// Scan finished naturally — send the authoritative final state.
		if a.scanID == currentScanID {
			if cached, ok := a.cache.Get(a.currentPath); ok {
				var total int64
				for _, e := range cached {
					if e.Size > 0 {
						total += e.Size
					}
				}
				trySend(scanUpdate{
					entries:   cached,
					total:     total,
					status:    status,
					dirsDone:  dirsDone,
					dirsTotal: dirsTotal,
					done:      true,
					scanTime:  time.Since(t0),
				})
			} else {
				upd := buildUpdate(true)
				trySend(upd)
			}
		}
	}()
}

func (a *App) maybePrefetch() {
	if a.scanning {
		return
	}
	if a.prefetchCancel != nil {
		a.prefetchCancel()
	}

	var target *DirEntry
	for _, e := range a.entries {
		if e.IsDir && e.Size > 0 && e.Error == "" {
			if _, ok := a.cache.Get(e.Path); !ok {
				target = e
				break
			}
		}
	}

	if target == nil {
		return
	}

	ctx, cancel := context.WithCancel(context.Background())
	a.prefetchCancel = cancel

	go func() {
		events := make(chan ScanEvent, 1)
		scanDirectory(ctx, target.Path, a.skipNet, events, a.cache)
		for range events {} // drain
	}()
}

func (a *App) moveCursor(delta int) {
	if len(a.entries) == 0 {
		return
	}
	a.cursor = clamp(a.cursor+delta, 0, len(a.entries)-1)
	lh := a.listHeight()
	if a.cursor < a.scroll {
		a.scroll = a.cursor
	} else if a.cursor >= a.scroll+lh {
		a.scroll = a.cursor - lh + 1
	}
}

func clamp(v, min, max int) int {
	if v < min { return min }
	if v > max { return max }
	return v
}

func (a *App) enterDir() {
	if len(a.entries) == 0 { return }
	ent := a.entries[a.cursor]
	if !ent.IsDir {
		a.setMessage("🚫 That's a file, not a folder!")
		return
	}
	if ent.Error == "permission denied" || ent.Error == "skipped" {
		a.setMessage("🔒 Access denied — this folder doesn't want visitors")
		return
	}
	a.history = append(a.history, navHistory{a.currentPath, a.cursor, a.scroll})
	a.currentPath = ent.Path
	a.startScan()
}

func (a *App) goBack() {
	parent := filepath.Dir(a.currentPath)
	if parent == a.currentPath {
		a.setMessage("🌍 Already at root — nowhere left to go!")
		return
	}
	if len(a.history) > 0 {
		prev := a.history[len(a.history)-1]
		if prev.path == parent {
			a.history = a.history[:len(a.history)-1]
			a.currentPath = parent
			a.startScan()
			// Restore position after scan finishes naturally is tricky in async.
			// For simplicity, rescan resets cursor. A real app would delay clearing entries.
			return
		}
	}
	a.history = nil
	a.currentPath = parent
	a.startScan()
}

func (a *App) deleteSelected() {
	if len(a.entries) == 0 { return }
	ent := a.entries[a.cursor]
	
	if ent.IsDir && protectedPaths[ent.Path] {
		a.setMessage("🚫 Cannot delete a protected system directory!")
		return
	}

	prompt := fmt.Sprintf(" 💀 Nuke '%s' (%s)? This is permanent! (y/N) ", ent.Name, strings.TrimSpace(humanSize(ent.Size)))
	w, _ := a.screen.Size()
	
	// Draw prompt
	style := tcell.StyleDefault.Background(tcell.ColorRed).Foreground(tcell.ColorWhite).Bold(true)
	fillLine(a.screen, a.listHeight()+2, w, style)
	drawString(a.screen, 0, a.listHeight()+2, truncate(prompt, w), style)
	a.screen.Show()

	// Wait for input
	for {
		ev := a.screen.PollEvent()
		if ev, ok := ev.(*tcell.EventKey); ok {
			if ev.Rune() == 'y' || ev.Rune() == 'Y' {
				var err error
				if ent.IsDir {
					err = os.RemoveAll(ent.Path)
				} else {
					err = os.Remove(ent.Path)
				}
				if err != nil {
					a.setMessage(fmt.Sprintf("😬 Error: %v", err))
				} else {
					a.cache.Invalidate(a.currentPath)
					a.setMessage(fmt.Sprintf("💥 Obliterated: %s", ent.Name))
					a.startScan()
				}
				return
			}
			a.setMessage("😅 Phew! Cancelled, nothing was harmed")
			return
		}
	}
}

func (a *App) openInManager() {
	var target string
	if len(a.entries) > 0 {
		target = a.entries[a.cursor].Path
	} else {
		target = a.currentPath
	}

	var cmd *exec.Cmd
	if runtime.GOOS == "darwin" {
		cmd = exec.Command("open", "-R", target)
	} else {
		// Try common Linux file managers
		for _, c := range []string{"nautilus", "dolphin", "thunar"} {
			if p, _ := exec.LookPath(c); p != "" {
				cmd = exec.Command(p, target)
				break
			}
		}
		if cmd == nil {
			cmd = exec.Command("xdg-open", filepath.Dir(target))
		}
	}
	
	if err := cmd.Start(); err != nil {
		a.setMessage(fmt.Sprintf("Could not open: %v", err))
	} else {
		a.setMessage("📂 Opened in file manager")
	}
}

// ═══════════════════════════════════════════════════════════════════════════════
// Drawing
// ═══════════════════════════════════════════════════════════════════════════════

func (a *App) draw() {
	w, h := a.screen.Size()
	if h < 6 || w < 40 {
		a.screen.Clear()
		drawString(a.screen, 0, 0, "Terminal too small 😬", tcell.StyleDefault)
		a.screen.Show()
		return
	}

	lh := a.listHeight()
	
	// Row 0: Title
	headerStyle := tcell.StyleDefault.Background(tcell.ColorDarkCyan).Foreground(tcell.ColorBlack).Bold(true)
	fillLine(a.screen, 0, w, headerStyle)
	
	title := ""
	if a.scanning {
		spinChars := "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
		spin := string([]rune(spinChars)[time.Now().Nanosecond()/100000000%len(spinChars)])
		title = fmt.Sprintf(" %s %s", spin, scanQuips[time.Now().Second()%len(scanQuips)])
	} else {
		title = fmt.Sprintf(" 🗂️  DiskVu — %s", a.currentPath)
	}
	drawString(a.screen, 0, 0, truncate(title, w), headerStyle)

	if !a.scanning {
		sizeStr := fmt.Sprintf(" %s total ", strings.TrimSpace(humanSize(a.totalSize)))
		drawString(a.screen, w-len([]rune(sizeStr)), 0, sizeStr, headerStyle)
	}

	// Row 1: Progress or Columns
	borderStyle := tcell.StyleDefault.Foreground(tcell.ColorBlue).Bold(true)
	statusStyle := tcell.StyleDefault.Foreground(tcell.ColorYellow).Bold(true)
	fillLine(a.screen, 1, w, borderStyle)

	if a.scanning {
		progress := ""
		if a.scanDirsTotal > 0 {
			frac := float64(a.scanDirsDone) / float64(a.scanDirsTotal)
			filled := int(frac * 10)
			bar := strings.Repeat("█", filled) + strings.Repeat("░", 10-filled)
			progress = fmt.Sprintf(" [%s] %d/%d dirs", bar, a.scanDirsDone, a.scanDirsTotal)
		}
		label := ""
		if a.scanStatus != "" {
			label = fmt.Sprintf("  ← %s", a.scanStatus)
		}
		row1 := fmt.Sprintf(" 📂 Sizing directories…%s%s", progress, label)
		drawString(a.screen, 0, 1, truncate(row1, w), statusStyle)
	} else {
        colHeader := "     SIZE      %  USAGE                 NAME"
		drawString(a.screen, 0, 1, colHeader, borderStyle)
	}

	// Rows 2..h-3: Entries
	if len(a.entries) == 0 && !a.scanning {
		drawString(a.screen, 2, 2, " ✨ Nothing here! Squeaky clean 🧹", tcell.StyleDefault.Foreground(tcell.ColorDarkCyan).Bold(true))
	}

	visStart := a.scroll
	visEnd := a.scroll + lh
	if visEnd > len(a.entries) { visEnd = len(a.entries) }

	for i := visStart; i < visEnd; i++ {
		y := 2 + (i - visStart)
		if y >= h-2 { break }
		ent := a.entries[i]
		selected := i == a.cursor
		a.drawEntry(y, w, ent, selected)
	}

	// Scrollbar
	if len(a.entries) > lh {
		total := len(a.entries)
		thumbH := max(1, lh*lh/total)
		thumbPos := a.scroll * (lh - thumbH) / max(1, total-lh)
		
		for y := 2; y < 2+lh; y++ {
			ch := '│'
			if y-2 >= thumbPos && y-2 < thumbPos+thumbH {
				ch = '█'
			}
			a.screen.SetContent(w-1, y, ch, nil, tcell.StyleDefault.Foreground(tcell.ColorBlue))
		}
	}

	// Footer (h-2)
	footerStyle := tcell.StyleDefault.Background(tcell.ColorDarkCyan).Foreground(tcell.ColorBlack).Bold(true)
	fillLine(a.screen, h-2, w, footerStyle)
	
	posStr := ""
	if len(a.entries) > 0 {
		posStr = fmt.Sprintf("  [%d/%d]", a.cursor+1, len(a.entries))
	}
	
	scanLabel := ""
	if a.scanTime < 500*time.Millisecond {
		scanLabel = fmt.Sprintf("⚡ %.2fs", a.scanTime.Seconds())
	} else {
		scanLabel = fmt.Sprintf("🏃 %.2fs", a.scanTime.Seconds())
	}

	footerLeft := fmt.Sprintf(" %d items  %s%s", len(a.entries), scanLabel, posStr)
	drawString(a.screen, 0, h-2, footerLeft, footerStyle)

	// Help Bar (h-1)
	helpStyle := tcell.StyleDefault.Foreground(tcell.ColorBlue)
	fillLine(a.screen, h-1, w, helpStyle)
	helpText := " ↑↓/jk:move  ↵/→/l:open  ←/h/BS:back  r:rescan  d:delete  ~:home  o:open  q:quit"
	drawString(a.screen, 0, h-1, helpText, helpStyle)

	// Toast Message
	if a.message != "" && time.Since(a.messageTime) < MsgTTL {
		msg := fmt.Sprintf("  %s  ", a.message)
		mx := max(0, (w-runewidth.StringWidth(msg))/2)
		my := h / 2
		style := tcell.StyleDefault.Background(tcell.ColorRed).Foreground(tcell.ColorWhite).Bold(true)
		drawString(a.screen, mx, my, msg, style)
	}

	a.screen.Show()
}

func (a *App) drawEntry(y, w int, ent *DirEntry, selected bool) {
	pending := ent.Size == PendingSize
	unknown := ent.Size == UnknownSize
	sizeStr := humanSize(ent.Size)
	sEmoji := sizeEmoji(ent.Size)

	pct := 0.0
	pctStr := "  ...%"
	if unknown {
		pctStr = "  ???%"
	} else if !pending && a.totalSize > 0 {
		pct = float64(ent.Size) / float64(a.totalSize) * 100
		pctStr = fmt.Sprintf("%5.1f%%", pct)
	}

	barStr := ""
	if unknown {
		barStr = strings.Repeat("?", BarWidth)
	} else if pending {
		barStr = strings.Repeat("·", BarWidth)
	} else {
		filled := int(pct / 100 * float64(BarWidth))
		sub := int((pct/100*float64(BarWidth) - float64(filled)) * 8)
		partialChars := " ▏▎▍▌▋▊▉"
		barStr = strings.Repeat("█", filled)
		if filled < BarWidth {
			if sub > 0 && sub < len(partialChars) {
				barStr += string([]rune(partialChars)[sub])
			} else {
				barStr += " "
			}
			barStr += strings.Repeat("░", BarWidth-filled-1)
		}
	}

	icon := fileIcon(ent.Name, ent.IsDir)
	name := fmt.Sprintf("%s %s", icon, ent.Name)
	if ent.IsDir {
		name += "/"
		if ent.ItemCount > 0 {
			name += fmt.Sprintf("  (%d items)", ent.ItemCount)
		}
	}
	if ent.Error == "network" {
		name += " 🌐"
	} else if ent.Error != "" && ent.Error != "symlink" {
		name += fmt.Sprintf(" ⚠️  [%s]", ent.Error)
	} else if ent.Error == "symlink" {
		name += " 🔗"
	}

	sizeColor := tcell.ColorGreen
	if pending { sizeColor = tcell.ColorBlue } 
	if unknown { sizeColor = tcell.ColorRed }
	if ent.Size > 1<<30 { sizeColor = tcell.ColorRed } 
	if ent.Size > 100<<20 { sizeColor = tcell.ColorYellow }

	nameColor := tcell.ColorWhite
	if ent.IsDir { nameColor = tcell.ColorDarkCyan }

	if selected {
		style := tcell.StyleDefault.Background(tcell.ColorWhite).Foreground(tcell.ColorBlack).Bold(true)
		fillLine(a.screen, y, w-1, style)
		col := 1
		col = drawString(a.screen, col, y, sEmoji, style)
		col += 1
		col = drawString(a.screen, col, y, sizeStr, style)
		col += 2
		col = drawString(a.screen, col, y, pctStr, style)
		col += 2
		col = drawString(a.screen, col, y, barStr, style)
		col += 2
		remaining := w - col - 2
		if remaining > 0 {
			drawString(a.screen, col, y, truncate(name, remaining), style)
		}
	} else {
		col := 1
		style := tcell.StyleDefault.Foreground(sizeColor)
		col = drawString(a.screen, col, y, sEmoji, style)
		col += 1
		drawString(a.screen, col, y, sizeStr, style.Bold(true))
		col += 9
		drawString(a.screen, col, y, pctStr, tcell.StyleDefault.Foreground(tcell.ColorDarkMagenta))
		col += 7
		drawString(a.screen, col, y, barStr, tcell.StyleDefault.Foreground(tcell.ColorGreen))
		col += BarWidth + 2
		remaining := w - col - 2
		if remaining > 0 {
			nStyle := tcell.StyleDefault.Foreground(nameColor)
			if ent.Error != "" && ent.Error != "symlink" {
				nStyle = tcell.StyleDefault.Foreground(tcell.ColorRed)
			}
			drawString(a.screen, col, y, truncate(name, remaining), nStyle)
		}
	}
}

// ═══════════════════════════════════════════════════════════════════════════════
// Main
// ═══════════════════════════════════════════════════════════════════════════════

func main() {
    defaultPath := "."
    if len(os.Args) > 1 {
        defaultPath = os.Args[1]
    }

    skipNet := flag.Bool("skip-network", false, "skip NFS, CIFS, and other network/remote filesystems")
    version := flag.Bool("V", false, "print version")
    flag.Parse()

    if *version {
        fmt.Printf("diskvu %s\n", Version)
        os.Exit(0)
    }

    args := flag.Args()
    path := defaultPath
    if len(args) > 0 {
        path = args[0]
    }

    absPath, err := filepath.Abs(path)
    if err != nil || absPath == "" {
        fmt.Fprintf(os.Stderr, "Error: invalid path '%s'\n", path)
        os.Exit(1)
    }

    info, err := os.Stat(absPath)
    if err != nil || !info.IsDir() {
        fmt.Fprintf(os.Stderr, "Error: '%s' is not a directory\n", absPath)
        os.Exit(1)
    }

    s, err := tcell.NewScreen()
    if err != nil {
        log.Fatalf("Error creating screen: %v", err)
    }
    if err := s.Init(); err != nil {
        log.Fatalf("Error initializing screen: %v", err)
    }
    defer s.Fini()

    s.SetStyle(tcell.StyleDefault.Background(tcell.ColorBlack).Foreground(tcell.ColorWhite))
    s.Clear()

    app := NewApp(s, absPath, *skipNet)
    app.startScan()

    // Handle OS signals
    sigCh := make(chan os.Signal, 1)
    signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM, syscall.SIGQUIT)

    // tcell.Screen doesn't have an Events channel, so we must poll in a goroutine
    // and forward events to a channel to use them in a select statement.
    eventCh := make(chan tcell.Event, 10)
    go func() {
        for {
            ev := s.PollEvent()
            if ev == nil {
                close(eventCh)
                return
            }
            eventCh <- ev
        }
    }()

    // UI Loop
    ticker := time.NewTicker(TickMs * time.Millisecond)
    defer ticker.Stop()

    for {
        select {
        case <-sigCh:
            if app.scanCancel != nil {
                app.scanCancel()
            }
            return

        case upd := <-app.scanUpdates:
            // Apply state from the scan goroutine — only safe place to mutate App.
            app.entries = upd.entries
            app.totalSize = upd.total
            app.scanStatus = upd.status
            app.scanDirsDone = upd.dirsDone
            app.scanDirsTotal = upd.dirsTotal
            if upd.done {
                app.scanning = false
                app.scanTime = upd.scanTime
                app.cursor = 0
                app.scroll = 0
                app.maybePrefetch()
            }
            app.draw()

        case <-ticker.C:
            app.draw()

        case ev, ok := <-eventCh:
            if !ok {
                return // Event channel closed
            }
            switch ev := ev.(type) {
            case *tcell.EventResize:
                s.Sync()
            case *tcell.EventKey:
                if ev.Key() == tcell.KeyEscape || ev.Rune() == 'q' || ev.Rune() == 'Q' {
                    if app.scanCancel != nil {
                        app.scanCancel()
                    }
                    return
                }

                switch ev.Key() {
                case tcell.KeyUp, tcell.KeyCtrlP:
                    app.moveCursor(-1)
                case tcell.KeyDown, tcell.KeyCtrlN:
                    app.moveCursor(1)
                case tcell.KeyPgUp:
                    app.moveCursor(-app.listHeight())
                case tcell.KeyPgDn:
                    app.moveCursor(app.listHeight())
                case tcell.KeyHome:
                    app.moveCursor(-len(app.entries))
                case tcell.KeyEnd:
                    app.moveCursor(len(app.entries))
                case tcell.KeyEnter, tcell.KeyRight:
                    app.enterDir()
                case tcell.KeyBackspace, tcell.KeyLeft:
                    app.goBack()
                }

                switch ev.Rune() {
                case 'k':
                    app.moveCursor(-1)
                case 'j':
                    app.moveCursor(1)
                case 'h':
                    app.goBack()
                case 'l':
                    app.enterDir()
                case 'g':
                    app.moveCursor(-len(app.entries))
                case 'G':
                    app.moveCursor(len(app.entries))
                case 'r':
                    if !app.scanning {
                        app.cache.Invalidate(app.currentPath)
                        app.setMessage("🔄 Fresh scan complete!")
                        app.startScan()
                    }
                case 'd':
                    if !app.scanning {
                        app.deleteSelected()
                    }
                case '~':
                    if !app.scanning {
                        if u, err := user.Current(); err == nil && u.HomeDir != app.currentPath {
                            app.history = nil
                            app.currentPath = u.HomeDir
                            app.startScan()
                        }
                    }
                case 'o':
                    app.openInManager()
                }
            }
        }
    }
}
