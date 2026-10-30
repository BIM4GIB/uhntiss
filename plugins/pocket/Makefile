#!/usr/bin/make -f
# Builds the three Pocket plug-ins (VST3 + CLAP everywhere, AU on macOS) into plugins/pocket/bin/.
#   make            native build          make universal   macOS arm64 + x86_64 (10.15+)
#   make clean      make params           regenerate <plugin>/PocketParams.h from the M4L builders (commit the result)
PLUGINS = pocket303 pocket101 pocketkick
all:
	@for p in $(PLUGINS); do $(MAKE) -C $$p all || exit 1; done
universal:
	@for p in $(PLUGINS); do $(MAKE) -C $$p macos-universal-10.15 || exit 1; done
params:
	python3 gen_params.py
clean:
	@for p in $(PLUGINS); do $(MAKE) -C $$p clean; done
	rm -rf bin
.PHONY: all universal params clean
