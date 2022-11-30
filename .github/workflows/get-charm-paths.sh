#!/bin/bash -x

# Finds the charms in this repo, outputing them as JSON
# Will return one of:
# * the relative paths of the directories listed in `./charms`, if that directory exists
# * "./", if the root directory has a "metadata.yaml" file
# * otherwise, error
#
# Modifed from: https://stackoverflow.com/questions/63517732/github-actions-build-matrix-for-lambda-functions/63736071#63736071
CHARMS_DIR="./charms"
if [ -d "$CHARMS_DIR" ];
then
  CHARM_PATHS=$(find $CHARMS_DIR -maxdepth 1 -type d -not -path '*/\.*' -not -path "$CHARMS_DIR")
else
  if [ -f "./metadata.yaml" ]
  then
    CHARM_PATHS="./"
  else
    echo "Cannot find valid charm directories - aborting"
    exit 1
  fi
fi

# Convert output to JSON string format
# { charm_paths: [...] }
CHARM_PATHS_LIST=$(echo "$CHARM_PATHS" | jq -c --slurp --raw-input 'split("\n")[:-1]')

echo "Found CHARM_PATHS_LIST: $CHARM_PATHS_LIST"

echo "::set-output name=CHARM_PATHS_LIST::$CHARM_PATHS_LIST"
