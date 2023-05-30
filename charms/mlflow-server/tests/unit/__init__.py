#
# Initialize unit tests
#

"""Setup test environment for unit tests."""

import ops.testing

# enable simulation of container networking
ops.testing.SIMULATE_CAN_CONNECT = True
