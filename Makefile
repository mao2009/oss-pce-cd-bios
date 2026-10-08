PYTHON ?= python3
.PHONY: check dev-rom test
check:
	$(PYTHON) tools/build_rom.py --check-source

test:
	$(PYTHON) -m unittest discover -s tests -v

dev-rom:
	$(PYTHON) tools/build_rom.py
