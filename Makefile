PREFIX     ?= /usr/local
BINDIR      = $(PREFIX)/bin
PYTHON     ?= python3

.PHONY: install uninstall check lint

install:
	@$(PYTHON) -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" \
		|| (echo "Error: Python 3.8+ required"; exit 1)
	install -d $(BINDIR)
	install -m 755 diskvu.py $(BINDIR)/diskvu
	@echo "Installed to $(BINDIR)/diskvu"

uninstall:
	rm -f $(BINDIR)/diskvu
	@echo "Removed $(BINDIR)/diskvu"

# User-local install (no sudo required)
install-user:
	mkdir -p ~/.local/bin
	cp diskvu.py ~/.local/bin/diskvu
	chmod +x ~/.local/bin/diskvu
	@echo "Installed to ~/.local/bin/diskvu"
	@echo "Make sure ~/.local/bin is in your PATH."

uninstall-user:
	rm -f ~/.local/bin/diskvu

check:
	$(PYTHON) -m py_compile diskvu.py && echo "Syntax OK"

lint:
	$(PYTHON) -m pyflakes diskvu.py 2>/dev/null || true
	$(PYTHON) -m mypy --ignore-missing-imports diskvu.py 2>/dev/null || true
