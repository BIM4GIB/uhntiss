# Pocket 303 placeholder firmware — Daisy Seed (any revision: Rev4/1.1/1.2/Seed2 DFM/Seed3)
#
#   make                 build build/pocket303.bin  (bench: pots ignored, CC-controlled)
#   make POTS=1          build for the finished box (8 pots on A0..A7 drive the voice)
#   make program-dfu     flash over USB (Seed in DFU mode: hold BOOT, tap RESET, release BOOT)
#
# libDaisy / DaisySP live outside the repo (see ../README.md); override the paths if needed:
#   make LIBDAISY_DIR=/path/to/libDaisy DAISYSP_DIR=/path/to/DaisySP

TARGET = pocket303
CPP_SOURCES = pocket303.cpp

LIBDAISY_DIR ?= $(HOME)/Daisy/libDaisy
DAISYSP_DIR  ?= $(HOME)/Daisy/DaisySP

# 0 = bare Seed on the bench (ADC pins float, so pots are ignored; use MIDI CCs)
# 1 = the box: pots are read and override the CC values
POTS ?= 0
C_DEFS += -DPOCKET303_POTS=$(POTS)

SYSTEM_FILES_DIR = $(LIBDAISY_DIR)/core
include $(SYSTEM_FILES_DIR)/Makefile
