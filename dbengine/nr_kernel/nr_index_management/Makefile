# NeurDB Index Management Extension Makefile

MODULE_big = nr_index_management
OBJS = nr_index_management.o

EXTENSION = nr_index_management
DATA = nr_index_management--1.0.sql \
       nr_index_management--1.1.sql \
       nr_index_management--1.2.sql \
       nr_index_management--1.0--1.1.sql \
       nr_index_management--1.1--1.2.sql

REGRESS = test_nr_index_management

# PostgreSQL build system
MAKEFILE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
NEURDB_ROOT_CANDIDATES := \
	$(abspath $(MAKEFILE_DIR)/../../..) \
	$(abspath $(MAKEFILE_DIR)/../../../..) \
	$(abspath $(MAKEFILE_DIR)/../../../../..) \
	/code/neurdb-dev
NEURDB_ROOT ?= $(firstword $(foreach d,$(NEURDB_ROOT_CANDIDATES),$(if $(wildcard $(d)/psql/bin/pg_config),$(d),)))
ifeq ($(strip $(NEURDB_ROOT)),)
$(error Could not find NeurDB root (missing psql/bin/pg_config). Set NEURDB_ROOT=/code/neurdb-dev or PG_CONFIG=/code/neurdb-dev/psql/bin/pg_config)
endif

PG_CONFIG ?= $(NEURDB_ROOT)/psql/bin/pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)

# Include NeurDB paths
override CPPFLAGS := -I$(NEURDB_ROOT)/dbengine/src/include/neurdb -I$(NEURDB_ROOT)/dbengine/nr_kernel $(CPPFLAGS)

# Use PostgreSQL server includes
override CPPFLAGS := -I$(NEURDB_ROOT)/psql/include/postgresql/server $(CPPFLAGS)
