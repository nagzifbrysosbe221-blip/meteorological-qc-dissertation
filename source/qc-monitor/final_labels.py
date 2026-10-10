"""Evaluation-only accepted labels; no detector/model imports.

Numeric background truth comes from the completed documentary review. Structural
truth is computed from current receipts and original inventory lineage. Synthetic
changes override a usable background only on their saved sparse positive mask.
"""
import json
from pathlib import Path
from datetime import datetime, timedelta


def load_labels(path, review_sha256):
    result = {}
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            r = json.loads(line)
            key = (r['station'],r['timestamp_utc'],r['variable'])
            if key in result or r['review_sha256'] != review_sha256:
                raise ValueError('Duplicate or unbound private numeric label')
            day = datetime.fromisoformat(r['source_date'])
            stamp = datetime.fromisoformat(r['timestamp_utc'])
            if ((stamp.replace(tzinfo=None)-day).total_seconds() != r['source_hour']*3600 or
                    r['target_label'] is not (r['station']==260) or
                    r['scored_period'] is not (r['scope']=='final') or
                    r['variable'] not in ('T','U') or r['station'] not in (240,260) or
                    type(r['source_hour']) is not int or not 1 <= r['source_hour'] <= 24):
                raise ValueError('Private label source/HH24/role mismatch')
            if r['numeric_truth'] is False and r['numeric_truth_state'] != 'assumed_normal_not_certified':
                raise ValueError('Numeric negative lost assumed-normal qualification')
            if r['numeric_truth'] is not None and type(r['numeric_truth']) is not bool:
                raise ValueError('Invalid private numeric truth')
            result[key] = r
    return result


def reviewed_truth(generated, window, labels, *, counterpart=False, background=False):
    # Import only at evaluation time, after completed predictions and raw episodes.
    from scenario_truth import private_truth
    truth = private_truth(generated, window, counterpart=counterpart)
    root = generated['root']
    positive = set() if counterpart or background else set(root['actual_change_mask'])
    originals = {r['original']['identity']:r['original'] for r in generated['lineage'] if 'original' in r}
    by_slot = {}
    for r in originals.values():
        if r['station']==260: by_slot.setdefault(r['timestamp'],set()).add(r['identity'])
    for key, item in truth['units'].items():
        case,task,subject,var = key.split('|')
        if background: item['primary_task'] = True
        if task != 'value': continue
        accepted = labels.get((260,subject,var))
        if accepted is None: raise ValueError('Missing accepted target label; no implicit negative')
        if set(accepted['record_ids']) != by_slot.get(subject,set()):
            raise ValueError('Private label does not match original receipt lineage')
        expected_month = (datetime.fromisoformat(subject)-timedelta(hours=1)).strftime('%Y-%m')
        if accepted['source_date'][:7] != expected_month:
            raise ValueError('Source month mismatch')
        item['source_month'] = expected_month
        item['review_sha256'] = accepted['review_sha256']
        item['background_truth_state'] = accepted['numeric_truth_state']
        # A missing/ambiguous current value is never a measured negative.
        if not item['usable']:
            item.update(truth=None, origin='unknown')
        elif (subject in positive and var == root['variable'] and
              root['family'] in ('gradual_bias','out_of_range')):
            item.update(truth=True,origin='synthetic_positive')
        else:
            value = accepted['numeric_truth']
            item.update(truth=value, origin='unknown' if value is None else
                        'natural_positive' if value else 'assumed_normal')
    truth['purpose'] = 'accepted private review labels plus independent structural/synthetic lineage'
    if background: truth['roots'] = []
    return truth
