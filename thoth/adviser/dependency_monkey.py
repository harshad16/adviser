#!/usr/bin/env python3
# thoth-adviser
# Copyright(C) 2018 Fridolin Pokorny
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


"""Dependency Monkey traverses dependency graph and generates stacks."""

import random
import os
from functools import partial
import typing
import logging

from thoth.adviser.python import Project
from thoth.adviser.python import DECISISON_FUNCTIONS
from thoth.adviser.python import DependencyGraph

_LOGGER = logging.getLogger(__name__)


def _dm_amun_inspect_wrapper(output: str, context: dict, generated_project: Project, count: int) -> typing.Optional[str]:
    """A wrapper around Amun inspection call."""
    context['python'] = generated_project.to_dict()
    try:
        response = amun_inspect(output, **context)
        _LOGGER.info("Submitted Amun inspection #%d: %r", count, response['inspection_id'])
        _LOGGER.debug("Full Amun response: %s", response)
        return response['inspection_id']
    except Exception as exc:
        _LOGGER.exception("Failed to submit stack to Amun analysis: %s", str(exc))

    return None


def _dm_amun_directory_output(output: str, generated_project: Project, count: int):
    """A wrapper for placing generated software stacks onto filesystem."""
    _LOGGER.debug("Writing stack %d", count)

    path = os.path.join(output, f'{count:05d}')
    os.makedirs(path, exist_ok=True)

    generated_project.to_files(os.path.join(path, 'Pipfile'), os.path.join(path, 'Pipfile.lock'))

    return path


def _dm_stdout_output(generated_project: Project, count: int):
    """A function called if the project should be printed to stdout as a dict."""
    json.dump(generated_project.to_dict(), fp=sys.stdout, sort_keys=True, indent=2)
    return None


def _fill_package_digests(generated_project: Project) -> Project:
    """Temporary fill package digests stated in Pipfile.lock."""
    from itertools import chain
    from thoth.adviser.configuration import config
    from thoth.adviser.python import Source

    # Pick the first warehouse for now.
    package_index = Source(config.warehouses[0])
    for package_version in chain(generated_project.pipfile_lock.packages,
                                 generated_project.pipfile_lock.dev_packages):
        if package_version.hashes:
            # Already filled from the last run.
            continue

        scanned_hashes = package_index.get_package_hashes(
            package_version.name,
            package_version.locked_version
        )

        for entry in scanned_hashes:
            package_version.hashes.append('sha256:' + entry['sha256'])

    return generated_project


def _do_dependency_monkey(project: Project, *, output_function: typing.Callable, decision_function: typing.Callable,
                          count: int = None, dry_run: bool = False) -> dict:
    """Run dependency monkey."""
    computed = 0
    result = {
        'output': [],
        'computed': 0,
    }
    dependency_graph = DependencyGraph.from_project(project)

    for generated_project in dependency_graph.walk(decision_function):
        computed += 1

        # TODO: we should pick digests of artifacts once we will have them in the graph database
        generated_project = _fill_package_digests(generated_project)

        if not dry_run:
            entry = output_function(generated_project, count=computed)
            if entry:
                result['output'].append(entry)

        if count is not None and computed >= count:
            break

    result['computed'] = computed
    return result


def dependency_monkey(project: Project, output: str, *, seed: int = None, decision: str = None,
                      dry_run: bool = False, context: str = None, count: int = None) -> dict:
    """Run Dependency Monkey on the given stack.

    @param project: a Python project to be used for generating software stacks (lockfile is not needed)
    @param output: output (Amun API, directory or '-' for stdout) where stacks should be written to
    @param seed: a seed to be used in case of random stack generation
    @param decision: decision function to be used
    @param dry_run: do not perform actual writing to output, just run the dependency monkey and report back computed stacks
    @param context: context to be sent to Amun, if output is set to be Amun
    @param count: generate upto N stacks
    """
    if decision not in DECISISON_FUNCTIONS:
        raise ValueError(f"Decision function {decision} is not known, available are: {list(DECISISON_FUNCTIONS.keys())}")

    decision_function = DECISISON_FUNCTIONS[decision]
    random.seed(seed)

    if count is not None and (count <= 0):
        _LOGGER.error("Number of stacks has to be a positive integer")
        return 3

    if output.startswith(('https://', 'http://')):
        # Submitting to Amun
        if context:
            try:
                context = json.loads(context)
            except Exception as exc:
                _LOGGER.error("Failed to load Amun context that should be passed with generated stacks: %s", str(exc))
                return 1
        else:
            context = {}
            _LOGGER.warning("Context to Amun API is empty")

        output_function = partial(_dm_amun_inspect_wrapper, output, context)
    elif output == '-':
        output_function = _dm_stdout_output
    else:
        if context:
            _LOGGER.error("Unable to use context when writing generated projects onto filesystem")
            return 2

        if not os.path.isdir(output):
            os.makedirs(output, exist_ok=True)

        output_function = partial(_dm_amun_directory_output, output)

    return _do_dependency_monkey(
        project,
        dry_run=dry_run,
        decision_function=decision_function,
        count=count,
        output_function=output_function
    )
