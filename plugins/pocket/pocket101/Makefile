#!/usr/bin/make -f
NAME = Pocket101
FILES_DSP = Plugin.cpp
DPF_BUILD_DIR = ../build/$(NAME)
DPF_TARGET_DIR = ../bin
PKG_CONFIG = false
include ../dpf/Makefile.plugins.mk
BUILD_CXX_FLAGS += -I../common
TARGETS = vst3 clap
ifeq ($(MACOS),true)
TARGETS += au
endif
all: $(TARGETS)
