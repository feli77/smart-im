"""Verify real librime-lua property callbacks using a separately loaded rime.dll.

This verifies property availability and atomic callback ordering, not actual TSF
context collection. Every generated schema, log and data file is written to a
new isolated subdirectory of --output-dir; installed Rime/user data is not changed.
Run with the same Python architecture as the chosen rime.dll (usually x64).
"""

import argparse
import ctypes as C
import json
import os
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--rime-dir", type=Path, required=True, help="Directory containing rime.dll and data"
)
parser.add_argument(
    "--output-dir",
    type=Path,
    default=Path(__file__).resolve().parents[1] / "artifacts/native-tests/librime-property",
    help="Parent directory for a new isolated probe directory",
)
args = parser.parse_args()
INSTALL = args.rime_dir.resolve()
output = args.output_dir.resolve()
if not (INSTALL / "rime.dll").is_file():
    parser.error("--rime-dir must contain rime.dll")
protected = [INSTALL]
if os.environ.get("APPDATA"):
    protected.append((Path(os.environ["APPDATA"]) / "Rime").resolve())
if any(output == path or path in output.parents for path in protected):
    parser.error("--output-dir must be outside the Rime installation and user-data directories")
output.mkdir(parents=True, exist_ok=True)
work = Path(tempfile.mkdtemp(prefix="probe-", dir=output))
(work / "build").mkdir()
(work / "logs").mkdir()
schema = """schema:
  schema_id: probe
  name: Property notifier probe
  version: '1'
engine:
  processors:
    - lua_processor@probe
  segmentors:
    - abc_segmentor
  translators: []
"""
default = "schema_list:\n  - schema: probe\n"
for path in [work, work / "build"]:
    (path / "probe.schema.yaml").write_text(schema, encoding="utf-8")
    (path / "default.yaml").write_text(default, encoding="utf-8")
(work / "rime.lua").write_text(
    """
probe = {
  init = function(env)
    local ctx = env.engine.context
    env.connection = ctx.property_update_notifier:connect(function(context, name)
      if name == 'smart_im_context' then
        local value = context:get_property(name)
        context:set_property('probe_seen', 'seen:' .. value)
        local count = tonumber(context:get_property('probe_count')) or 0
        context:set_property('probe_count', tostring(count + 1))
      end
    end)
    ctx:set_property('probe_ready', 'yes')
  end,
  fini = function(env) env.connection:disconnect() end,
  func = function(key, env) return 2 end,
}
""",
    encoding="utf-8",
)


class Traits(C.Structure):
    _fields_ = [
        ("data_size", C.c_int),
        ("shared_data_dir", C.c_char_p),
        ("user_data_dir", C.c_char_p),
        ("distribution_name", C.c_char_p),
        ("distribution_code_name", C.c_char_p),
        ("distribution_version", C.c_char_p),
        ("app_name", C.c_char_p),
        ("modules", C.POINTER(C.c_char_p)),
        ("min_log_level", C.c_int),
        ("log_dir", C.c_char_p),
        ("prebuilt_data_dir", C.c_char_p),
        ("staging_dir", C.c_char_p),
    ]


def path_bytes(path):
    return str(path).encode("utf-8")


modules = (C.c_char_p * 3)(b"default", b"lua", None)
traits = Traits(
    C.sizeof(Traits) - C.sizeof(C.c_int),
    path_bytes(INSTALL / "data"),
    path_bytes(work),
    b"probe",
    b"probe",
    b"1",
    b"rime.smart_im_probe",
    modules,
    2,
    path_bytes(work / "logs"),
    path_bytes(INSTALL / "data"),
    path_bytes(work / "build"),
)
dll = C.CDLL(str(INSTALL / "rime.dll"))
for name, restype, argtypes in [
    ("RimeSetup", None, [C.POINTER(Traits)]),
    ("RimeInitialize", None, [C.POINTER(Traits)]),
    ("RimeCreateSession", C.c_size_t, []),
    ("RimeSelectSchema", C.c_int, [C.c_size_t, C.c_char_p]),
    ("RimeSetProperty", None, [C.c_size_t, C.c_char_p, C.c_char_p]),
    ("RimeGetProperty", C.c_int, [C.c_size_t, C.c_char_p, C.c_void_p, C.c_size_t]),
    ("RimeDestroySession", C.c_int, [C.c_size_t]),
    ("RimeFinalize", None, []),
]:
    function = getattr(dll, name)
    function.restype, function.argtypes = restype, argtypes
dll.RimeSetup(C.byref(traits))
dll.RimeInitialize(C.byref(traits))
session = dll.RimeCreateSession()
assert session


def get(name):
    buffer = C.create_string_buffer(4096)
    assert dll.RimeGetProperty(session, name.encode(), buffer, len(buffer))
    return buffer.value.decode()


try:
    assert dll.RimeSelectSchema(session, b"probe")
    assert get("probe_ready") == "yes", "Lua processor did not initialize"
    values = [
        "1\tfield-1\t" + "已有正文".encode().hex() + "\t" + "后文".encode().hex(),
        "",
        "1\tfield-2\t\t",
    ]
    for count, value in enumerate(values + [values[-1]], 1):
        dll.RimeSetProperty(session, b"smart_im_context", value.encode())
        assert get("probe_seen") == "seen:" + value
        assert get("probe_count") == str(count)
    print(
        json.dumps(
            {
                "result": "pass",
                "notifier": True,
                "atomic_value_visible_in_callback": True,
                "same_value_notifies": True,
                "writes": str(work),
            },
            ensure_ascii=False,
        )
    )
finally:
    dll.RimeDestroySession(session)
    dll.RimeFinalize()
