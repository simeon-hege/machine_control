# -*- coding: utf-8 -*-

import ctypes
import os
import platform
from decimal import Decimal


EW_OK = 0


class FocasError(Exception):
    def __init__(self, func_name, code):
        self.func_name = func_name
        self.code = code
        super().__init__(f"{func_name} failed with FOCAS code {code}")


class POSELM(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_int32),
        ("dec", ctypes.c_short),
        ("unit", ctypes.c_short),
        ("disp", ctypes.c_short),
        ("name", ctypes.c_char),
        ("suff", ctypes.c_char),
    ]


class ODBPOS(ctypes.Structure):
    _fields_ = [
        ("abs", POSELM),
        ("mach", POSELM),
        ("rel", POSELM),
        ("dist", POSELM),
    ]


class ODBSYS(ctypes.Structure):
    _fields_ = [
        ("addinfo", ctypes.c_short),
        ("max_axis", ctypes.c_short),
        ("cnc_type", ctypes.c_char * 2),
        ("mt_type", ctypes.c_char * 2),
        ("series", ctypes.c_char * 4),
        ("version", ctypes.c_char * 4),
        ("axes", ctypes.c_char * 2),
    ]


class ODBM(ctypes.Structure):
    _fields_ = [
        ("datano", ctypes.c_short),
        ("dummy", ctypes.c_short),
        ("mcr_val", ctypes.c_int32),
        ("dec_val", ctypes.c_short),
    ]


class FocasClient:
    def __init__(self, host, port=8193, timeout=10, library_path=None):
        self.host = host
        self.port = int(port)
        self.timeout = int(timeout)
        self._lib = self._load_library(library_path)
        self._configure_signatures()
        self._handle = ctypes.c_ushort(0)
        self._connected = False

    def _load_library(self, library_path=None):
        module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        machine = platform.machine().lower()
        arch_map = {
            "x86_64": "linux-x86_64",
            "amd64": "linux-x86_64",
            "i386": "linux-x86",
            "i686": "linux-x86",
            "aarch64": "linux-aarch64",
            "arm64": "linux-aarch64",
            "armv7l": "linux-armv7",
        }
        platform_dir = arch_map.get(machine)

        bundled_candidates = []
        if platform_dir:
            bundled_candidates.append(os.path.join(module_root, "lib", platform_dir, "libfwlib32.so"))

        bundled_candidates.extend([
            os.path.join(module_root, "lib", "linux-x86_64", "libfwlib32.so"),
            os.path.join(module_root, "lib", "linux-x86", "libfwlib32.so"),
            os.path.join(module_root, "lib", "linux-armv7", "libfwlib32.so"),
            os.path.join(module_root, "lib", "linux-aarch64", "libfwlib32.so"),
        ])

        candidates = [
            *bundled_candidates,
            library_path,
            os.environ.get("FOCAS_LIB_PATH"),
            "libfwlib32.so",
            "libfwlib32.so.1",
        ]

        unique_candidates = []
        for candidate in candidates:
            if candidate and candidate not in unique_candidates:
                unique_candidates.append(candidate)

        last_error = None
        for candidate in unique_candidates:
            try:
                return ctypes.CDLL(candidate)
            except OSError as exc:
                last_error = exc

        raise OSError(
            "Unable to load libfwlib32 shared library. Deploy it under module lib/<platform>/libfwlib32.so or set FOCAS_LIB_PATH."
        ) from last_error

    def _configure_signatures(self):
        self._lib.cnc_allclibhndl3.argtypes = [
            ctypes.c_char_p,
            ctypes.c_ushort,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_ushort),
        ]
        self._lib.cnc_allclibhndl3.restype = ctypes.c_short

        self._lib.cnc_freelibhndl.argtypes = [ctypes.c_ushort]
        self._lib.cnc_freelibhndl.restype = ctypes.c_short

        self._lib.cnc_sysinfo.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBSYS)]
        self._lib.cnc_sysinfo.restype = ctypes.c_short

        self._lib.cnc_rdposition.argtypes = [
            ctypes.c_ushort,
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_short),
            ctypes.POINTER(ODBPOS),
        ]
        self._lib.cnc_rdposition.restype = ctypes.c_short

        self._lib.cnc_wrmacro.argtypes = [
            ctypes.c_ushort,
            ctypes.c_short,
            ctypes.c_short,
            ctypes.c_long,
            ctypes.c_short,
        ]
        self._lib.cnc_wrmacro.restype = ctypes.c_short

        self._lib.cnc_rdmacro.argtypes = [
            ctypes.c_ushort,
            ctypes.c_short,
            ctypes.c_short,
            ctypes.POINTER(ODBM),
        ]
        self._lib.cnc_rdmacro.restype = ctypes.c_short

    def _check(self, func_name, result_code):
        if result_code != EW_OK:
            raise FocasError(func_name, int(result_code))

    @staticmethod
    def _decode_ascii(value):
        if isinstance(value, (bytes, bytearray)):
            return value.decode("ascii", errors="ignore").strip("\x00 ")
        return str(value)

    @staticmethod
    def _pose_to_dict(pose):
        decimals = int(pose.dec)
        scale = 10 ** decimals if decimals >= 0 else 1
        return {
            "raw": int(pose.data),
            "dec": decimals,
            "value": float(int(pose.data) / scale),
            "unit": int(pose.unit),
            "disp": int(pose.disp),
            "name": FocasClient._decode_ascii(pose.name),
            "suffix": FocasClient._decode_ascii(pose.suff),
        }

    def connect(self):
        rc = self._lib.cnc_allclibhndl3(
            self.host.encode("ascii"),
            self.port,
            self.timeout,
            ctypes.byref(self._handle),
        )
        self._check("cnc_allclibhndl3", rc)
        self._connected = True

    def close(self):
        if not self._connected:
            return
        rc = self._lib.cnc_freelibhndl(self._handle)
        self._check("cnc_freelibhndl", rc)
        self._connected = False

    def read_sysinfo(self):
        sysinfo = ODBSYS()
        rc = self._lib.cnc_sysinfo(self._handle, ctypes.byref(sysinfo))
        self._check("cnc_sysinfo", rc)

        return {
            "addinfo": int(sysinfo.addinfo),
            "max_axis": int(sysinfo.max_axis),
            "cnc_type": self._decode_ascii(sysinfo.cnc_type),
            "mt_type": self._decode_ascii(sysinfo.mt_type),
            "series": self._decode_ascii(sysinfo.series),
            "version": self._decode_ascii(sysinfo.version),
            "axes": self._decode_ascii(sysinfo.axes),
        }

    def read_position(self, max_axes=32, position_type=-1):
        axis_count = ctypes.c_short(max_axes)
        values = (ODBPOS * max_axes)()

        rc = self._lib.cnc_rdposition(self._handle, position_type, ctypes.byref(axis_count), values)
        self._check("cnc_rdposition", rc)

        result = []
        for index in range(int(axis_count.value)):
            row = values[index]
            result.append({
                "axis_index": index,
                "absolute": self._pose_to_dict(row.abs),
                "machine": self._pose_to_dict(row.mach),
                "relative": self._pose_to_dict(row.rel),
                "distance": self._pose_to_dict(row.dist),
            })
        return result

    def write_macro(self, macro_no, value, decimals=4):
        decimals = int(decimals)
        scaled_value = int(round(float(value) * (10 ** decimals)))
        rc = self._lib.cnc_wrmacro(
            self._handle,
            int(macro_no),
            10,
            scaled_value,
            decimals,
        )
        self._check("cnc_wrmacro", rc)

    def read_macro(self, macro_no):
        data = ODBM()
        rc = self._lib.cnc_rdmacro(self._handle, int(macro_no), 10, ctypes.byref(data))
        self._check("cnc_rdmacro", rc)

        decimals = int(data.dec_val)
        scale = 10 ** decimals if decimals >= 0 else 1
        return {
            "macro_no": int(data.datano),
            "raw": int(data.mcr_val),
            "decimals": decimals,
            "value": float(data.mcr_val) / scale,
        }

    def __enter__(self):
        # Ensure a benign fwlibeth.log exists in the current working
        # directory. The fwlib native library attempts to open a
        # relative-named "fwlibeth.log" and will abort if it's missing.
        try:
            cwd_log = os.path.join(os.getcwd(), "fwlibeth.log")
            if not os.path.exists(cwd_log):
                open(cwd_log, "a").close()
                try:
                    os.chmod(cwd_log, 0o644)
                except Exception:
                    pass
        except Exception:
            pass

        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
