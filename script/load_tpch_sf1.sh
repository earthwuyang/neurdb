for t in nation region part supplier partsupp customer orders lineitem; do
  sed 's/|$//' "/Volumes/data/DB/tpch-dbgen/tpch-sf1/${t}.tbl" | \
    psql -h localhost -p 15432 -U neurdb -d tpch_sf1 \
      -c "COPY $t FROM STDIN WITH (FORMAT csv, DELIMITER '|');"
done
