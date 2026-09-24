#!/usr/bin/env python3
"""Mappings built by displays, dict() calls, unions and update() report
through the key a sender lands on."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_dict_stores import (  # noqa: E402
    _PRE_CLEAN, _body, _verdict)


def test_dict_unpack_first_reports(tmp):
    assert _verdict(tmp, _body(
        'x = {"k": relay(), **args.__dict__}["k"]', 'x()')) == (1, 1)


def test_dict_unpack_last_reports(tmp):
    assert _verdict(tmp, _body(
        'x = {**args.__dict__, "k": relay()}["k"]', 'x()')) == (1, 1)


def test_dict_unpack_first_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = {"k": relay(), **args.__dict__}["k"]', 'x()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dict_unpack_last_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = {**args.__dict__, "k": relay()}["k"]', 'x()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_nested_unpack_reports(tmp):
    assert _verdict(tmp, _body(
        'x = {**{"k": relay()}, **args.__dict__}["k"]', 'x()')) == (1, 1)


def test_nested_unpack_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = {**{"k": relay()}, **args.__dict__}["k"]', 'x()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dynamic_key_merge_reports(tmp):
    assert _verdict(tmp, _body(
        'd = {}\nd[str(args.chrome_tab)] = relay()\n'
        'x = {**d, **args.__dict__}', 'x["323"]()')) == (1, 1)


def test_dynamic_key_merge_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'd = {}\nd[str(args.chrome_tab)] = relay()\n'
        'x = {**d, **args.__dict__}', 'x["323"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dict_kwargs_unpack_reports(tmp):
    assert _verdict(tmp, _body(
        'x = dict(k=relay(), **args.__dict__)["k"]', 'x()')) == (1, 1)


def test_dict_kwargs_unpack_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = dict(k=relay(), **args.__dict__)["k"]', 'x()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dict_known_kw_reports(tmp):
    assert _verdict(tmp, _body(
        'x = dict({"k": relay()}, other=ordinary)["k"]', 'x()')) == (1, 1)


def test_dict_known_kw_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = dict({"k": relay()}, other=ordinary)["k"]', 'x()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dict_local_call_reports(tmp):
    assert _verdict(tmp, _body(
        'def opaque_call():\n'
        '    return {"k": relay()}\n'
        'x = dict(opaque_call())["k"]', 'x()')) == (1, 1)


def test_dict_local_call_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'def opaque_call():\n'
        '    return {"k": relay()}\n'
        'x = dict(opaque_call())["k"]', 'x()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_or_known_left_reports(tmp):
    assert _verdict(tmp, _body(
        'x = {"k": relay()} | vars(args)', 'x["k"]()')) == (1, 1)


def test_or_known_left_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = {"k": relay()} | vars(args)', 'x["k"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_or_known_right_reports(tmp):
    assert _verdict(tmp, _body(
        'x = vars(args) | {"k": relay()}', 'x["k"]()')) == (1, 1)


def test_or_known_right_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = vars(args) | {"k": relay()}', 'x["k"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_or_opaque_name_reports(tmp):
    assert _verdict(tmp, _body(
        'o = args.__dict__\nx = {"k": relay()} | o', 'x["k"]()')) == (1, 1)


def test_or_opaque_name_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'o = args.__dict__\nx = {"k": relay()} | o', 'x["k"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def _opaque(store, held='send'):
    return (f'send = ext_cmd\nargs.box = {{"k": {held}}}\n{store}\n'
            'return x["k"]("_focus", "focus-tab", tab=args.chrome_tab)')


def test_opaque_part_display_unpack_reports(tmp):
    assert _verdict(tmp, _opaque('x = {"j": 1, **args.box}')) == (1, 1)


def test_opaque_part_display_unpack_clean_is_unprovable(tmp):
    assert _verdict(tmp, _opaque(
        'x = {"j": 1, **args.box}', 'ordinary')) == (0, 1)


def test_opaque_part_dict_call_unpack_reports(tmp):
    assert _verdict(tmp, _opaque('x = dict(j=1, **args.box)')) == (1, 1)


def test_opaque_part_dict_call_unpack_clean_is_unprovable(tmp):
    assert _verdict(tmp, _opaque(
        'x = dict(j=1, **args.box)', 'ordinary')) == (0, 1)


def test_opaque_part_or_name_reports(tmp):
    assert _verdict(tmp, _opaque(
        'o = args.box\nx = {"j": 1} | o')) == (1, 1)


def test_opaque_part_or_name_clean_is_unprovable(tmp):
    assert _verdict(tmp, _opaque(
        'o = args.box\nx = {"j": 1} | o', 'ordinary')) == (0, 1)


def test_opaque_part_or_attribute_reports(tmp):
    assert _verdict(tmp, _opaque('x = {"j": 1} | args.box')) == (1, 1)


def test_opaque_part_or_attribute_clean_is_unprovable(tmp):
    assert _verdict(tmp, _opaque(
        'x = {"j": 1} | args.box', 'ordinary')) == (0, 1)


def _pairs(store, held):
    return (f'send = ext_cmd\n{store.format(held)}\n'
            'return x["k"]("_focus", "focus-tab", tab=args.chrome_tab)')


def test_dict_call_pair_list_stays_clean(tmp):
    assert _verdict(tmp, _pairs(
        'x = dict([("k", {})])', 'ordinary')) == (0, 0)


def test_dict_call_pair_list_reports(tmp):
    assert _verdict(tmp, _pairs(
        'x = dict([("k", {})])', 'send')) == (1, 1)


def test_dict_call_pair_name_stays_clean(tmp):
    assert _verdict(tmp, _pairs(
        'p = [("k", {})]\nx = dict(p)', 'ordinary')) == (0, 0)


def test_dict_call_pair_name_reports(tmp):
    assert _verdict(tmp, _pairs(
        'p = [("k", {})]\nx = dict(p)', 'send')) == (1, 1)


def test_dict_call_display_unpack_reports(tmp):
    assert _verdict(tmp, _body(
        'x = dict(**{"k": relay()})', 'x["k"]()')) == (1, 1)


def test_dict_call_display_unpack_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = dict(**{"k": relay()})', 'x["k"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dict_call_name_unpack_reports(tmp):
    assert _verdict(tmp, _body(
        'b = {"k": relay()}\nx = dict(**b)', 'x["k"]()')) == (1, 1)


def test_dict_call_name_unpack_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'b = {"k": relay()}\nx = dict(**b)', 'x["k"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_dict_call_keyword_and_unpack_reports(tmp):
    assert _verdict(tmp, _body(
        'b = {"k": relay()}\nx = dict(j=1, **b)', 'x["k"]()')) == (1, 1)


def test_dict_call_keyword_and_unpack_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'b = {"k": relay()}\nx = dict(j=1, **b)', 'x["k"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


_DYNAMIC_PAIRS = ('x = dict([(str(args.chrome_tab), relay()), '
                  '(str(args.flag), ordinary)])')


def test_dict_call_dynamic_pair_keys_report(tmp):
    assert _verdict(tmp, _body(_DYNAMIC_PAIRS, 'x["323"]()')) == (1, 1)


def test_dict_call_dynamic_pair_keys_stay_clean(tmp):
    assert _verdict(tmp, _body(
        _DYNAMIC_PAIRS, 'x["323"]()', prefix=_PRE_CLEAN)) == (0, 0)


def _update_pairs(held):
    return ('send = ext_cmd\nx = {}\n'
            f'x.update([(str(args.chrome_tab), {held}), '
            '(str(args.flag), lambda *a, **k: 0)])\n'
            'return x["323"]("_focus", "focus-tab", tab=args.chrome_tab)')


def test_update_dynamic_pair_keys_report(tmp):
    assert _verdict(tmp, _update_pairs('send')) == (1, 1)


def test_update_dynamic_pair_keys_stay_clean(tmp):
    assert _verdict(tmp, _update_pairs('ordinary')) == (0, 0)


_UPDATE_AFTER_DYNAMIC = ('x = {}\nx[str(args.chrome_tab)] = relay()\n'
                         'x.update({str(args.flag): ordinary})')


def test_update_after_dynamic_store_reports(tmp):
    assert _verdict(tmp, _body(
        _UPDATE_AFTER_DYNAMIC, 'x["323"]()')) == (1, 1)


def test_update_after_dynamic_store_stays_clean(tmp):
    assert _verdict(tmp, _body(
        _UPDATE_AFTER_DYNAMIC, 'x["323"]()', prefix=_PRE_CLEAN)) == (0, 0)


def test_display_dynamic_key_reports(tmp):
    assert _verdict(tmp, _body(
        'x = {str(args.chrome_tab): relay()}', 'x["323"]()')) == (1, 1)


def test_display_dynamic_key_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = {str(args.chrome_tab): relay()}', 'x["323"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


_DYNAMIC_AND_OPAQUE = 'x = {str(args.chrome_tab): relay(), **args.__dict__}'


def test_display_dynamic_key_beside_opaque_reports(tmp):
    assert _verdict(tmp, _body(
        _DYNAMIC_AND_OPAQUE, 'x["323"]()')) == (1, 1)


def test_display_dynamic_key_beside_opaque_stays_clean(tmp):
    assert _verdict(tmp, _body(
        _DYNAMIC_AND_OPAQUE, 'x["323"]()', prefix=_PRE_CLEAN)) == (0, 0)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictbuild_')


if __name__ == '__main__':
    raise SystemExit(main())
