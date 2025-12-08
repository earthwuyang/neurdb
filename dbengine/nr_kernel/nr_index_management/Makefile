# NeurDB Index Management Extension Makefile

MODULE_big = nr_index_management
OBJS = nr_index_management.o

EXTENSION = nr_index_management
DATA = nr_index_management--1.0.sql

REGRESS = test_nr_index_management

# PostgreSQL build system
PG_CONFIG = /code/neurdb-dev/psql/bin/pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)

# Include NeurDB paths
override CPPFLAGS := -I/code/neurdb-dev/dbengine/src/include/neurdb -I/code/neurdb-dev/dbengine/nr_kernel $(CPPFLAGS)

# Use PostgreSQL server includes
override CPPFLAGS := -I/code/neurdb-dev/psql/include/postgresql/server $(CPPFLAGS)