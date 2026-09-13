#!/bin/sh
set -eu
# Databases are created explicitly by the platform bootstrap. This command only
# installs/upgrades the official schemas, never drops databases or data.
for kind in temporal visibility; do
  if [ "$kind" = temporal ]; then
    export SQL_DATABASE=hrs_temporal_v2
  else
    export SQL_DATABASE=hrs_temporal_visibility_v2
  fi
  temporal-sql-tool setup-schema -v 0.0
  temporal-sql-tool update-schema -d "/etc/temporal/schema/postgresql/v12/$kind/versioned"
done
