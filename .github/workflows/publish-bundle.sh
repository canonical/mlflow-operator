set -ex

# Publishes a bundle to the latest/edge branch in charmhub using charmcraft

BUNDLE_NAME=$(yq e '.name' bundle.yaml)
echo Found BUNDLE_NAME=$BUNDLE_NAME

charmcraft pack | tee publishlog_charmcraft_pack.log

# Parse name of bundle file from above output
BUNDLE_FILE=$(cat publishlog_charmcraft_pack.log | cut -d "'" -f 2)
echo Found BUNDLE_FILE=$BUNDLE_FILE

charmcraft upload $BUNDLE_FILE | tee publishlog_charmcraft_upload.log

# Parse revision number from upload output
REVISION=$(cat publishlog_charmcraft_upload.log | cut -d " " -f 2)
echo Found REVISION=$REVISION

charmcraft release $BUNDLE_NAME --revision $REVISION --channel=edge

