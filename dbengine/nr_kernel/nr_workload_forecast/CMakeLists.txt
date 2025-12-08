cmake_minimum_required(VERSION 3.10)
project(nr_workload_forecast)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Find PostgreSQL
find_package(PostgreSQL REQUIRED)

# Find OpenSSL for MD5
find_package(OpenSSL REQUIRED)

# Check if PostgreSQL is found
if(NOT PostgreSQL_FOUND)
    message(FATAL_ERROR "PostgreSQL not found")
endif()

# Get PostgreSQL directories using pg_config
# Force using the custom pg_config from /code/neurdb-dev/psql/bin
set(PG_CONFIG "/code/neurdb-dev/psql/bin/pg_config")
if(EXISTS ${PG_CONFIG})
    execute_process(
        COMMAND ${PG_CONFIG} --pkglibdir
        OUTPUT_VARIABLE PG_PKGLIBDIR
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    execute_process(
        COMMAND ${PG_CONFIG} --sharedir
        OUTPUT_VARIABLE PG_SHAREDIR
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    execute_process(
        COMMAND ${PG_CONFIG} --includedir-server
        OUTPUT_VARIABLE PG_INCLUDEDIR_SERVER
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    message(STATUS "PostgreSQL directories from pg_config:")
    message(STATUS "  Package lib dir: ${PG_PKGLIBDIR}")
    message(STATUS "  Share dir: ${PG_SHAREDIR}")
    message(STATUS "  Server includes: ${PG_INCLUDEDIR_SERVER}")
else()
    # Fallback to CMake variables (may be empty on some systems)
    set(PG_PKGLIBDIR ${PostgreSQL_PKGLIBDIR})
    set(PG_SHAREDIR ${PostgreSQL_SHAREDIR})
    set(PG_INCLUDEDIR_SERVER "${PostgreSQL_INCLUDE_DIRS}")
    message(WARNING "pg_config not found, using CMake variables (may fail)")
endif()

# Set defaults if still empty
if(NOT PG_PKGLIBDIR OR PG_PKGLIBDIR STREQUAL "")
    set(PG_PKGLIBDIR "/usr/lib/postgresql/14/lib")
    message(WARNING "PostgreSQL package library directory not found, using default: ${PG_PKGLIBDIR}")
endif()

if(NOT PG_SHAREDIR OR PG_SHAREDIR STREQUAL "")
    set(PG_SHAREDIR "/usr/share/postgresql/14")
    message(WARNING "PostgreSQL share directory not found, using default: ${PG_SHAREDIR}")
endif()

# Source files
set(SOURCES
    src/nr_workload_forecast.c
    src/query_logger.c
    src/http_client.c
)

# Create shared library (PostgreSQL extension)
add_library(${PROJECT_NAME} MODULE ${SOURCES})

# Set properties
set_target_properties(${PROJECT_NAME} PROPERTIES
    PREFIX ""
    OUTPUT_NAME ${PROJECT_NAME}
)

# Include directories
target_include_directories(${PROJECT_NAME} PRIVATE
    ${PG_INCLUDEDIR_SERVER}
    src/include
)

# Link libraries
target_link_libraries(${PROJECT_NAME}
    ${PostgreSQL_LIBRARIES}
    OpenSSL::SSL
    OpenSSL::Crypto
)

# Compiler flags
target_compile_options(${PROJECT_NAME} PRIVATE
    -Wall
    -Wextra
    -Wmissing-prototypes
    -Wpointer-arith
    -Wendif-labels
    -Wmissing-format-attribute
    -Wimplicit-fallthrough
    -Werror=vla
    -fstack-protector-strong
    -fpic
    -fPIC
)

# Platform-specific settings
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_link_libraries(${PROJECT_NAME}
        -Wl,--build-id
        -Wl,--as-needed
    )
endif()

# Installation
install(TARGETS ${PROJECT_NAME}
    LIBRARY DESTINATION ${PG_PKGLIBDIR}
)

# Install extension files
install(FILES
    ${PROJECT_NAME}.control
    sql/${PROJECT_NAME}--1.0.sql
    DESTINATION ${PG_SHAREDIR}/extension
)

# Print configuration
message(STATUS "Building nr_workload_forecast extension")
message(STATUS "PostgreSQL version: ${PostgreSQL_VERSION_STRING}")
message(STATUS "PostgreSQL include dirs: ${PostgreSQL_INCLUDE_DIRS}")
message(STATUS "PostgreSQL library dirs: ${PostgreSQL_LIBRARY_DIRS}")
message(STATUS "PostgreSQL share dir: ${PG_SHAREDIR}")
message(STATUS "PostgreSQL package lib dir: ${PG_PKGLIBDIR}")

# Add custom target for testing
add_custom_target(test_ext
    COMMAND ${CMAKE_COMMAND} -E echo "Run tests with: make install && make test"
)

# Add clean target for generated files
set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_MAKE_CLEAN_FILES
    "${CMAKE_CURRENT_BINARY_DIR}/*.so"
)
