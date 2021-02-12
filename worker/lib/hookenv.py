#
# Stripped version of "hookenv.py" (By Erik Lönroth, 2020 erik.lonroth@gmail.com)
#
#
# Copyright 2014-2015 Canonical Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"Interactions with the Juju environment"
# Copyright 2013 Canonical Ltd.
#
# Authors:
#  Charm Helpers Developers <juju@lists.ubuntu.com>

from __future__ import print_function
from enum import Enum
from functools import wraps
import os
import json
import yaml
import subprocess
import sys
import errno
import tempfile
from subprocess import CalledProcessError

import six
if not six.PY3:
    from UserDict import UserDict
else:
    from collections import UserDict


CRITICAL = "CRITICAL"
ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"
DEBUG = "DEBUG"
TRACE = "TRACE"
MARKER = object()
SH_MAX_ARG = 131071

class WORKLOAD_STATES(Enum):
    ACTIVE = 'active'
    BLOCKED = 'blocked'
    MAINTENANCE = 'maintenance'
    WAITING = 'waiting'

cache = {}

def cached(func):
    """Cache return values for multiple executions of func + args

    For example::

        @cached
        def unit_get(attribute):
            pass

        unit_get('test')

    will cache the result of unit_get + 'test' for future calls.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        global cache
        key = json.dumps((func, args, kwargs), sort_keys=True, default=str)
        try:
            return cache[key]
        except KeyError:
            pass  # Drop out of the exception handler scope.
        res = func(*args, **kwargs)
        cache[key] = res
        return res
    wrapper._wrapped = func
    return wrapper


def flush(key):
    """Flushes any entries from function cache where the
    key is found in the function+args """
    flush_list = []
    for item in cache:
        if key in item:
            flush_list.append(item)
    for item in flush_list:
        del cache[item]


def log(message, level=None):
    """Write a message to the juju log"""
    command = ['juju-log']
    if level:
        command += ['-l', level]
    if not isinstance(message, six.string_types):
        message = repr(message)
    command += [message[:SH_MAX_ARG]]
    # Missing juju-log should not cause failures in unit tests
    # Send log output to stderr
    try:
        subprocess.call(command)
    except OSError as e:
        if e.errno == errno.ENOENT:
            if level:
                message = "{}: {}".format(level, message)
            message = "juju-log: {}".format(message)
            print(message, file=sys.stderr)
        else:
            raise

def relation_type():
    """The scope for the current relation hook"""
    return os.environ.get('JUJU_RELATION', None)

@cached
def relation_id(relation_name=None, service_or_unit=None):
    """The relation ID for the current or a specified relation"""
    if not relation_name and not service_or_unit:
        return os.environ.get('JUJU_RELATION_ID', None)
    elif relation_name and service_or_unit:
        service_name = service_or_unit.split('/')[0]
        for relid in relation_ids(relation_name):
            remote_service = remote_service_name(relid)
            if remote_service == service_name:
                return relid
    else:
        raise ValueError('Must specify neither or both of relation_name and service_or_unit')


def local_unit():
    """Local unit ID"""
    return os.environ['JUJU_UNIT_NAME']


def remote_unit():
    """The remote unit for the current relation hook"""
    return os.environ.get('JUJU_REMOTE_UNIT', None)


@cached
def remote_service_name(relid=None):
    """The remote service name for a given relation-id (or the current relation)"""
    if relid is None:
        unit = remote_unit()
    else:
        units = related_units(relid)
        unit = units[0] if units else None
    return unit.split('/')[0] if unit else None

@cached
def relation_get(attribute=None, unit=None, rid=None):
    """Get relation information"""
    _args = ['relation-get', '--format=json']
    if rid:
        _args.append('-r')
        _args.append(rid)
    _args.append(attribute or '-')
    if unit:
        _args.append(unit)
    try:
        return json.loads(subprocess.check_output(_args).decode('UTF-8'))
    except ValueError:
        return None
    except CalledProcessError as e:
        if e.returncode == 2:
            return None
        raise


def relation_set(relation_id=None, relation_settings=None, **kwargs):
    """Set relation information for the current unit"""
    relation_settings = relation_settings if relation_settings else {}
    relation_cmd_line = ['relation-set']
    accepts_file = "--file" in subprocess.check_output(
        relation_cmd_line + ["--help"], universal_newlines=True)
    if relation_id is not None:
        relation_cmd_line.extend(('-r', relation_id))
    settings = relation_settings.copy()
    settings.update(kwargs)
    for key, value in settings.items():
        # Force value to be a string: it always should, but some call
        # sites pass in things like dicts or numbers.
        if value is not None:
            settings[key] = "{}".format(value)
    if accepts_file:
        # --file was introduced in Juju 1.23.2. Use it by default if
        # available, since otherwise we'll break if the relation data is
        # too big. Ideally we should tell relation-set to read the data from
        # stdin, but that feature is broken in 1.23.2: Bug #1454678.
        with tempfile.NamedTemporaryFile(delete=False) as settings_file:
            settings_file.write(yaml.safe_dump(settings).encode("utf-8"))
        subprocess.check_call(
            relation_cmd_line + ["--file", settings_file.name])
        os.remove(settings_file.name)
    else:
        for key, value in settings.items():
            if value is None:
                relation_cmd_line.append('{}='.format(key))
            else:
                relation_cmd_line.append('{}={}'.format(key, value))
        subprocess.check_call(relation_cmd_line)
    # Flush cache of any relation-gets for local unit
    flush(local_unit())


def relation_clear(r_id=None):
    ''' Clears any relation data already set on relation r_id '''
    settings = relation_get(rid=r_id,
                            unit=local_unit())
    for setting in settings:
        if setting not in ['public-address', 'private-address']:
            settings[setting] = None
    relation_set(relation_id=r_id,
                 **settings)


@cached
def relation_ids(reltype=None):
    """A list of relation_ids"""
    reltype = reltype or relation_type()
    relid_cmd_line = ['relation-ids', '--format=json']
    if reltype is not None:
        relid_cmd_line.append(reltype)
        return json.loads(
            subprocess.check_output(relid_cmd_line).decode('UTF-8')) or []
    return []


@cached
def related_units(relid=None):
    """A list of related units"""
    relid = relid or relation_id()
    units_cmd_line = ['relation-list', '--format=json']
    if relid is not None:
        units_cmd_line.extend(('-r', relid))
    return json.loads(
        subprocess.check_output(units_cmd_line).decode('UTF-8')) or []


def function_get(key=None):
    """Gets the value of an action parameter, or all key/value param pairs"""
    cmd = ['function-get']
    # Fallback for older charms.
    if not cmd_exists('function-get'):
        cmd = ['action-get']

    if key is not None:
        cmd.append(key)
    cmd.append('--format=json')
    function_data = json.loads(subprocess.check_output(cmd).decode('UTF-8'))
    return function_data


def expected_related_units(reltype=None):
    """Get a generator for units we expect to join relation based on
    goal-state.

    Note that you can not use this function for the peer relation, take a look
    at expected_peer_units() for that.

    This function will raise KeyError if you request information for a
    relation type for which juju goal-state does not have information.  It will
    raise NotImplementedError if used with juju versions without goal-state
    support.

    Example usage:
    log('participant {} of {} joined relation {}'
        .format(len(related_units()),
                len(list(expected_related_units())),
                relation_type()))

    :param reltype: Relation type to list data for, default is to list data for
                    the realtion type we are currently executing a hook for.
    :type reltype: str
    :returns: iterator
    :rtype: types.GeneratorType
    :raises: KeyError, NotImplementedError
    """
    if not has_juju_version("2.4.4"):
        # goal-state existed in 2.4.0, but did not list individual units to
        # join a relation in 2.4.1 through 2.4.3. (LP: #1794739)
        raise NotImplementedError("goal-state relation unit count")
    reltype = reltype or relation_type()
    _goal_state = goal_state()
    return (key for key in _goal_state['relations'][reltype] if '/' in key)


@cached
def relation_for_unit(unit=None, rid=None):
    """Get the json represenation of a unit's relation"""
    unit = unit or remote_unit()
    relation = relation_get(unit=unit, rid=rid)
    for key in relation:
        if key.endswith('-list'):
            relation[key] = relation[key].split()
    relation['__unit__'] = unit
    return relation


@cached
def relations_for_id(relid=None):
    """Get relations of a specific relation ID"""
    relation_data = []
    relid = relid or relation_ids()
    for unit in related_units(relid):
        unit_data = relation_for_unit(unit, relid)
        unit_data['__relid__'] = relid
        relation_data.append(unit_data)
    return relation_data


@cached
def metadata():
    """Get the current charm metadata.yaml contents as a python object"""
    with open(os.path.join(charm_dir(), 'metadata.yaml')) as md:
        return yaml.safe_load(md)


@cached
def relation_types():
    """Get a list of relation types supported by this charm"""
    rel_types = []
    md = metadata()
    for key in ('provides', 'requires', 'peers'):
        section = md.get(key)
        if section:
            rel_types.extend(section.keys())
    return rel_types


@cached
def role_and_interface_to_relations(role, interface_name):
    """
    Given a role and interface name, return a list of relation names for the
    current charm that use that interface under that role (where role is one
    of ``provides``, ``requires``, or ``peers``).

    :returns: A list of relation names.
    """
    _metadata = metadata()
    results = []
    for relation_name, relation in _metadata.get(role, {}).items():
        if relation['interface'] == interface_name:
            results.append(relation_name)
    return results


@cached
def interface_to_relations(interface_name):
    """
    Given an interface, return a list of relation names for the current
    charm that use that interface.

    :returns: A list of relation names.
    """
    results = []
    for role in ('provides', 'requires', 'peers'):
        results.extend(role_and_interface_to_relations(role, interface_name))
    return results

@cached
def unit_get(attribute):
    """Get the unit ID for the remote unit"""
    _args = ['unit-get', '--format=json', attribute]
    try:
        return json.loads(subprocess.check_output(_args).decode('UTF-8'))
    except ValueError:
        return None

def charm_dir():
    """Return the root directory of the current charm"""
    d = os.environ.get('JUJU_CHARM_DIR')
    if d is not None:
        return d
    return os.environ.get('CHARM_DIR')


def cmd_exists(cmd):
    """Return True if the specified cmd exists in the path"""
    return any(
        os.access(os.path.join(path, cmd), os.X_OK)
        for path in os.environ["PATH"].split(os.pathsep)
    )

def status_set(workload_state, message, application=False):
    """Set the workload state with a message

    Use status-set to set the workload state with a message which is visible
    to the user via juju status. If the status-set command is not found then
    assume this is juju < 1.23 and juju-log the message instead.

    workload_state   -- valid juju workload state. str or WORKLOAD_STATES
    message          -- status update message
    application      -- Whether this is an application state set
    """
    bad_state_msg = '{!r} is not a valid workload state'

    if isinstance(workload_state, str):
        try:
            # Convert string to enum.
            workload_state = WORKLOAD_STATES[workload_state.upper()]
        except KeyError:
            raise ValueError(bad_state_msg.format(workload_state))

    if workload_state not in WORKLOAD_STATES:
        raise ValueError(bad_state_msg.format(workload_state))

    cmd = ['status-set']
    if application:
        cmd.append('--application')
    cmd.extend([workload_state.value, message])
    try:
        ret = subprocess.call(cmd)
        if ret == 0:
            return
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
    log_message = 'status-set failed: {} {}'.format(workload_state.value,
                                                    message)
    log(log_message, level='INFO')