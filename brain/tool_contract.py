"""Validation and conservative receipts at the model/tool boundary."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re

MUTATIONS = {
    'home': {'turn_on', 'turn_off', 'toggle'},
    'records': {'remember', 'log', 'birth', 'harvest', 'relate'},
    'memory': {'write'}, 'recipe': {'save'}, 'media': {'add'},
    'reminder': {'create', 'cancel'}, 'shopping': {'add', 'remove', 'clear'},
}
REQUIRED = {
    ('home', 'turn_on'): ('entity_id',), ('home', 'turn_off'): ('entity_id',),
    ('home', 'toggle'): ('entity_id',),
    ('records', 'remember'): ('name', 'kind'), ('records', 'birth'): ('name',),
    ('records', 'harvest'): ('bed', 'crop', 'qty'), ('records', 'entity'): ('name',),
    ('records', 'relate'): ('name', 'rel', 'to'),
    ('memory', 'write'): ('content',), ('memory', 'recall'): ('content',),
    ('recipe', 'save'): ('name', 'content'), ('recipe', 'lookup'): ('name',),
    ('reminder', 'create'): ('when',), ('reminder', 'cancel'): ('id',),
    ('shopping', 'add'): ('items',), ('shopping', 'remove'): ('items',),
    ('search', 'search'): ('query',), ('search', 'fetch'): ('url',),
}


def mutation(name, args):
    return isinstance(args, dict) and args.get('action', args.get('op')) in MUTATIONS.get(name, set())


def validate(name, args, schemas):
    """Validate the advertised schema subset, with strict JSON types and action fields."""
    schema = next((s['function']['parameters'] for s in schemas
                   if s['function']['name'] == name), None)
    if schema is None:
        return f"Error: tool '{name}' is not allowed for this request."
    if not isinstance(args, dict):
        return 'Error: tool arguments must be a JSON object.'
    try:
        if len(json.dumps(args, allow_nan=False).encode()) > 32768:
            return 'Error: tool arguments exceed the size limit.'
    except (TypeError, ValueError):
        return 'Error: arguments must contain finite JSON values.'

    def check(value, spec, path):
        kind = spec.get('type')
        types = {'object': dict, 'array': list, 'string': str, 'integer': int,
                 'number': (int, float), 'boolean': bool}
        if kind in types and (not isinstance(value, types[kind]) or
                              (kind in ('integer', 'number') and isinstance(value, bool))):
            return f'{path} must be {kind}'
        if 'enum' in spec and value not in spec['enum']:
            return f'{path} must be one of {spec["enum"]}'
        if kind == 'object':
            props = spec.get('properties')
            for key in spec.get('required', []):
                if key not in value:
                    return f'{path}.{key} is required'
            for key, child in value.items():
                if props is not None and key not in props:
                    return f'{path}.{key} is not recognized'
                if props and (err := check(child, props[key], f'{path}.{key}')):
                    return err
        if kind == 'array':
            if len(value) > 100:
                return f'{path} has too many items'
            for child in value:
                if err := check(child, spec.get('items', {}), path):
                    return err
        return None

    if err := check(args, schema, name):
        return f'Error: bad arguments for {name}: {err}'
    action = args.get('action', args.get('op'))
    for key in REQUIRED.get((name, action), ()):
        if key not in args or args[key] is None or (isinstance(args[key], str) and not args[key].strip()):
            return f'Error: {name}.{action} requires {"quantity (qty)" if key == "qty" else key}.'
    if name == 'records' and action == 'log' and not (args.get('did') or args.get('detail')):
        return 'Error: records.log requires did or detail; an empty event is not recorded.'
    if name == 'records' and action == 'harvest' and args['qty'] <= 0:
        return 'Error: harvest quantity must be positive.'
    if name == 'home' and action != 'get_state':
        if not re.fullmatch(r'light\.[a-z0-9_]+', args['entity_id']):
            return 'Error: home mutations require one exact light entity_id.'
        if 'brightness_pct' in args and not 0 <= args['brightness_pct'] <= 100:
            return 'Error: brightness_pct must be between 0 and 100.'
    return None


@dataclass(frozen=True)
class ToolResult:
    status: str
    data: str
    operation_id: str = ''
    error_code: str = ''
    retryable: bool = False

    def message(self):
        return json.dumps(asdict(self), ensure_ascii=False)


def receipt(name, args, text, outcome='ok', operation_id=''):
    """Legacy tools return prose. Only recognized write acknowledgements prove success.

    Unknown backend failures may occur after a remote commit. Never label those safely
    retryable. New tools should eventually return ToolResult directly.
    """
    text = str(text)
    if outcome == 'capacity':
        return ToolResult('failed', text, operation_id, 'capacity', True)
    if outcome != 'ok' or text.startswith('Operation outcome unknown'):
        return ToolResult('unknown', text, operation_id, 'outcome_unknown')
    if not mutation(name, args):
        failed = text.startswith(('Error', 'Shopping list backend error', 'Home Assistant returned'))
        return ToolResult('failed' if failed else 'succeeded', text, operation_id,
                          'backend_error' if failed else '', failed)
    success = {
        'records': ('Remembered ', 'Logged ', 'Recorded puppy ', 'Linked '),
        'memory': ('Remembered ',), 'recipe': ('Saved ', 'Updated '),
        'reminder': ('Reminder #', 'Reminder cancelled.'),
        'shopping': ('Added to ', 'Already on it:', 'Took off ', 'Cleared '),
        'home': ('Done ', 'Sent '), 'media': ('Added ', 'Queued ', 'Already '),
    }
    if (text.startswith(success.get(name, ())) or
            (name == 'media' and 'is already in the library' in text and 'search' in text)):
        return ToolResult('succeeded', text, operation_id)
    # These paths reject before dispatch or before their store write.
    if name == 'reminder' and text.startswith(("I couldn't read", 'No pending reminder', 'Which reminder')):
        return ToolResult('failed', text, operation_id, 'invalid_target_or_time')
    return ToolResult('unknown', text, operation_id, 'unverified_write')
