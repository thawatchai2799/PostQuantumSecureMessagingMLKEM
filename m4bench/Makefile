# m4bench - on-device benchmark for NUCLEO-F446RE / NUCLEO-F411RE (Cortex-M4)
#
#   make            firmware: build/m4bench.elf, build/m4bench.bin, build/m4bench.map
#   make host       host build of the same benchmark (logic check): build/m4bench_host
#   make vector     regenerate kem_vector.h (needs OQS_INSTALL_PATH for the liboqs cross-check)
#   make data       regenerate m4_data.h from corpus/ and data/results_c_sizes.csv
#   make clean
#
# Run from the repository root:  make -C m4bench
# OPT selects the optimization level of every codec and of ML-KEM (default -O2,
# the level of the host timing drivers; Table 8's code sizes use -Os).

ROOT   := ..
TP     := $(ROOT)/third_party
BUILD  := build
OPT    ?= -O2
REPS   ?= 5

CROSS  := arm-none-eabi-
ARCH   := -mcpu=cortex-m4 -mthumb -mfloat-abi=soft
WARN   := -Wall -Wextra
INC    := -I. -I$(ROOT) -I$(TP)/heatshrink -I$(TP)/unishox2 -I$(TP)/mlkem-native/mlkem
DEFS   := -DMLK_CONFIG_PARAMETER_SET=512 -DREPS=$(REPS) -DOPT_NAME='"$(OPT)"'

SRCS   := bench_core.c kem_glue.c $(ROOT)/codec.c \
          $(TP)/heatshrink/heatshrink_encoder.c $(TP)/heatshrink/heatshrink_decoder.c \
          $(TP)/unishox2/unishox2.c $(TP)/mlkem-native/mlkem/mlkem_native.c

FW_CFLAGS := $(ARCH) $(OPT) -std=gnu99 $(WARN) -Wno-unused-parameter $(INC) $(DEFS) -DPLAT_STM32 \
             -ffunction-sections -fdata-sections -fno-common -g
FW_LDFLAGS := $(ARCH) -nostartfiles --specs=nano.specs -T link.ld -Wl,--gc-sections \
              -Wl,-Map=$(BUILD)/m4bench.map -Wl,--print-memory-usage

all: $(BUILD)/m4bench.bin

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/m4bench.elf: $(SRCS) plat_stm32.c plat.h plat_stm32_inline.h m4_data.h kem_vector.h link.ld | $(BUILD)
	$(CROSS)gcc $(FW_CFLAGS) $(SRCS) plat_stm32.c $(FW_LDFLAGS) -o $@
	$(CROSS)size $@

$(BUILD)/m4bench.bin: $(BUILD)/m4bench.elf
	$(CROSS)objcopy -O binary $< $@
	@echo "firmware: $@ ($$(wc -c < $@) bytes)"

host: $(BUILD)/m4bench_host
$(BUILD)/m4bench_host: $(SRCS) plat_host.c plat.h m4_data.h kem_vector.h | $(BUILD)
	gcc -std=gnu99 $(OPT) $(WARN) -Wno-unused-parameter $(INC) $(DEFS) $(SRCS) plat_host.c -o $@

OQS_SO := $(firstword $(wildcard $(OQS_INSTALL_PATH)/lib/liboqs.so $(OQS_INSTALL_PATH)/lib64/liboqs.so))
ifneq ($(OQS_SO),)
OQS_FLAGS := -DHAVE_LIBOQS -I$(OQS_INSTALL_PATH)/include $(OQS_SO) -Wl,-rpath,$(dir $(OQS_SO))
endif

vector: | $(BUILD)
	gcc -std=c99 -O2 $(WARN) $(INC) -DMLK_CONFIG_PARAMETER_SET=512 make_kem_vector.c kem_glue.c \
	    $(TP)/mlkem-native/mlkem/mlkem_native.c -o $(BUILD)/make_kem_vector $(OQS_FLAGS)
	$(BUILD)/make_kem_vector kem_vector.h

data:
	cd $(ROOT) && python3 m4bench/make_m4_data.py

clean:
	rm -rf $(BUILD)

.PHONY: all host vector data clean
