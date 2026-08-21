# For building for the current running version of Linux
ifndef TARGET
TARGET = $(shell uname -r)
endif
# Or specific version
#TARGET = 2.6.33.5

KERNEL_MODULES = /lib/modules/$(TARGET)

ifneq ("","$(wildcard /usr/src/linux-headers-$(TARGET)/*)")
# Ubuntu
KERNEL_BUILD = /usr/src/linux-headers-$(TARGET)
else
ifneq ("","$(wildcard /usr/src/kernels/$(TARGET)/*)")
# Fedora
KERNEL_BUILD = /usr/src/kernels/$(TARGET)
else
KERNEL_BUILD = $(KERNEL_MODULES)/build
endif
endif

# SYSTEM_MAP = $(KERNEL_BUILD)/System.map
ifneq ("","$(wildcard /boot/System.map-$(TARGET))")
SYSTEM_MAP = /boot/System.map-$(TARGET)
else
# Arch
SYSTEM_MAP = /proc/kallsyms
endif

DRIVER := it87
# If DRIVER_VERSION is provided by DKMS or a caller, don't override it.
ifndef DRIVER_VERSION
# Prefer a VERSION file if it exists (e.g. in /usr/src/it87-<version>)
ifneq ("","$(wildcard VERSION)")
DRIVER_VERSION := $(shell cat VERSION)
# Else, if this looks like a git repo, derive version from git metadata
else ifneq ("","$(wildcard .git/*)")
DRIVER_VERSION := $(shell \
		    GIT_DESC=$$(git describe --long --always 2>/dev/null || echo unknown); \
		    GIT_DATE=$$(git show -s --format=%ci HEAD 2>/dev/null || date -u +'%Y-%m-%d 00:00:00'); \
		    DATE_PART=$$(date -u -d "$$GIT_DATE" +%Y%m%d 2>/dev/null || date -u +%Y%m%d); \
		    echo "$$GIT_DESC.$$DATE_PART" )
# Final fallback if there's no VERSION file and no usable git info
else
DRIVER_VERSION := unknown
endif
endif

# Directory below /lib/modules/$(TARGET)/kernel into which to install
# the module:
MOD_SUBDIR = drivers/hwmon
MODDESTDIR=$(KERNEL_MODULES)/kernel/$(MOD_SUBDIR)

obj-m = $(patsubst %,%.o,$(DRIVER))
obj-ko  := $(patsubst %,%.ko,$(DRIVER))

MAKEFLAGS += --no-print-directory

ifneq ("","$(wildcard $(MODDESTDIR)/*.ko.gz)")
COMPRESS_GZIP := y
endif
ifneq ("","$(wildcard $(MODDESTDIR)/*.ko.xz)")
COMPRESS_XZ := y
endif


.PHONY: all install modules modules_install clean

all: modules


# Targets for running make directly in the external module directory:

IT87_CFLAGS=-DIT87_DRIVER_VERSION='\"$(DRIVER_VERSION)\"'

modules:
	@$(MAKE) EXTRA_CFLAGS="$(IT87_CFLAGS)" -C $(KERNEL_BUILD) M=$(CURDIR) $@

clean:
	@$(MAKE) -C $(KERNEL_BUILD) M=$(CURDIR) $@

install: modules_install

modules_install:
	mkdir -p $(MODDESTDIR)
	cp $(DRIVER).ko $(MODDESTDIR)/
ifeq ($(COMPRESS_GZIP), y)
	@gzip -f $(MODDESTDIR)/$(DRIVER).ko
endif
ifeq ($(COMPRESS_XZ), y)
	@xz -f -C crc32 $(MODDESTDIR)/$(DRIVER).ko
endif
	depmod -a -F $(SYSTEM_MAP) $(TARGET)
