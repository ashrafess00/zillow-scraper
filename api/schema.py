"""
Schema post-processing for the RapidAPI-facing OpenAPI document.

RapidAPI's importer builds its endpoint groups from the root-level `tags`
declaration, and falls back to the `operationId` prefix for any operation it
cannot place. drf-spectacular auto-generates ids like `agentByLocation_retrieve`
and `similarHomes_list`, so the import produced the three real groups (Agents,
Properties, Utilities) *plus* one empty group per endpoint named after its
operationId prefix.

The root tags are declared in SPECTACULAR_SETTINGS['TAGS']; this hook handles the
other half by stripping the auto-generated method suffix, which also gives the
listing clean endpoint names ("similarHomes" rather than "similarHomes_list").
"""

# drf-spectacular appends the DRF action name to every operationId. None of these
# carry meaning for this API — every endpoint is a GET on its own path.
_ACTION_SUFFIXES = (
    '_retrieve',
    '_list',
    '_create',
    '_partial_update',
    '_update',
    '_destroy',
)

_HTTP_METHODS = frozenset(
    {'get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'}
)


def _strip_action_suffix(operation_id: str) -> str:
    for suffix in _ACTION_SUFFIXES:
        if operation_id.endswith(suffix):
            return operation_id[: -len(suffix)]
    return operation_id


def clean_operation_ids(result, generator, request, public):
    """
    Strip drf-spectacular's action suffix from every operationId.

    Collisions are left alone rather than silently merged: two operations
    sharing an id would make the document invalid, which is worse than the
    cosmetic suffix. That cannot happen while each path has a single GET, but
    adding a second method to a path would trigger it.
    """
    paths = result.get('paths') or {}

    # Work out the post-strip name for every operation first, so a collision can
    # be detected before anything is rewritten.
    proposed = {}
    for path, path_item in paths.items():
        for method, operation in (path_item or {}).items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get('operationId')
            if operation_id:
                proposed[(path, method)] = _strip_action_suffix(operation_id)

    seen = {}
    for key, new_id in proposed.items():
        seen.setdefault(new_id, []).append(key)

    for key, new_id in proposed.items():
        if len(seen[new_id]) > 1:
            continue  # ambiguous — keep the suffix that disambiguates it
        path, method = key
        paths[path][method]['operationId'] = new_id

    return result
