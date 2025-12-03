## Building a Custom `psqlparse` for the `moqoe` Conda Environment

The stock `psqlparse` (v1.0rc7) bundles PostgreSQL sources that only ship x86 atomics headers. On `aarch64` Pythons (the `moqoe` env), `pip install psqlparse` fails with:

```
fatal error: port/atomics/arch-arm.h: No such file or directory
```

To use `psqlparse` inside `moqoe`, build it from source with the missing ARM headers copied in. The following steps assume you already activated the `moqoe` environment (`conda activate moqoe`) and are working under `/code/neurdb-dev`.

1. **Prepare directories**
   ```bash
   mkdir -p /code/neurdb-dev/psqlparse/build && cd /code/neurdb-dev/psqlparse/build
   pip download psqlparse==1.0rc7
   tar xf psqlparse-1.0rc7.tar.gz
   cd psqlparse-1.0rc7
   ```

2. **Fetch a matching PostgreSQL tarball and copy the ARM headers**
   `psqlparse` vendors PostgreSQL 13; copy the missing atomics header(s) from the upstream tarball:
   ```bash
   curl -O https://ftp.postgresql.org/pub/source/v13.13/postgresql-13.13.tar.gz
   tar xf postgresql-13.13.tar.gz
   cp postgresql-13.13/src/include/port/atomics/arch-arm.h \
      libpg_query/src/postgres/include/port/atomics/
   ```
   If the build later complains about other headers (for example `arch-arm64.h`), copy them the same way.

3. **Build and install the patched package**
   ```bash
   pip install .
   ```
   or build a wheel for reuse:
   ```bash
   pip wheel .          # produces psqlparse-1.0rc7-*.whl
   pip install ./psqlparse-1.0rc7-*.whl
   ```

4. **Reference the patched package**
   When recreating the conda env, point `environment_moqoe.yml` to your wheel (e.g. `psqlparse @ file:///code/neurdb-dev/psqlparse/build/psqlparse-1.0rc7-custom.whl`) or keep this README and steps handy to rebuild after `conda env create`.

This process keeps the `psqlparse` dependency working on ARM while avoiding repeated manual patching. Remember to redo it whenever you update `psqlparse` or rebuild the environment from scratch.
