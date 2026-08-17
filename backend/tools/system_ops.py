"""系统操作内置工具：环境变量、Windows 注册表、管理员运行（UAC 提权）。

全部为 Windows 优先实现（注册表/提权依赖 win32 API）；非 Windows 平台返回明确错误。
"""
import asyncio
import os
from typing import Any, Dict, List, Optional

if os.name == "nt":
    import ctypes
    import winreg
else:
    ctypes = None
    winreg = None

from core.logger import get_logger

logger = get_logger("tools.system_ops")

# 敏感环境变量键（值脱敏显示）
_SENSITIVE_ENV_KEYS = {"password", "token", "api_key", "apikey", "secret", "authorization",
                       "aws_secret_access_key", "openai_api_key", "deepseek_api_key"}
_MAX_VALUE_LEN = 500  # 环境变量/注册表值列表展示截断


# ---------------------------------------------------------------- 环境变量

def _user_env_persistent(name: str) -> Optional[str]:
    """读取用户级持久环境变量（HKCU\\Environment，Windows）。"""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                value, _ = winreg.QueryValueEx(key, name)
                return str(value)
            except FileNotFoundError:
                return None
    except Exception as e:
        logger.warning(f"读取用户环境变量失败 {name}: {e}")
        return None


def _broadcast_env_change() -> None:
    """广播 WM_SETTINGCHANGE，让新启动的进程读到更新后的用户环境变量。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            0, 2000, ctypes.byref(result))
    except Exception as e:
        logger.warning(f"广播环境变量变更失败: {e}")


async def env_get(name: str) -> Dict[str, Any]:
    """读取环境变量（进程内值 + 用户级持久值）。"""
    name = str(name or "").strip()
    if not name:
        return {"error": "参数 name 不能为空"}
    return {
        "name": name,
        "value": os.environ.get(name),
        "persistent": _user_env_persistent(name),
        "exists": name in os.environ,
    }


async def env_set(name: str, value: str, persistent: bool = True) -> Dict[str, Any]:
    """设置环境变量：进程内立即生效；persistent=True 同时写入用户级持久值（新进程生效）。"""
    name = str(name or "").strip()
    if not name:
        return {"error": "参数 name 不能为空"}
    if name.lower() in _SENSITIVE_ENV_KEYS:
        return {"error": f"禁止设置敏感环境变量: {name}（防止误覆盖凭据）"}
    os.environ[name] = str(value)
    persisted = False
    if persistent:
        if os.name != "nt":
            return {"error": "非 Windows 平台不支持持久化环境变量，仅进程内生效",
                    "value": str(value), "persistent": False}
        try:
            import winreg
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment",
                                    0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, str(value))
            _broadcast_env_change()
            persisted = True
        except Exception as e:
            return {"error": f"持久化失败（进程内已生效）: {e}",
                    "value": str(value), "persistent": False}
    return {"name": name, "value": str(value), "persistent": persisted,
            "status": "success", "note": "进程内立即生效" + ("；已写入用户环境变量（新进程生效）" if persisted else "")}


async def env_delete(name: str, persistent: bool = True) -> Dict[str, Any]:
    """删除环境变量（进程内 + 可选用户级持久值）。"""
    name = str(name or "").strip()
    if not name:
        return {"error": "参数 name 不能为空"}
    removed_process = os.environ.pop(name, None) is not None
    removed_persistent = False
    if persistent and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, name)
                    removed_persistent = True
                except FileNotFoundError:
                    pass
            _broadcast_env_change()
        except Exception as e:
            return {"error": f"删除持久值失败（进程内已删）: {e}"}
    return {"name": name, "status": "success",
            "removed_process": removed_process, "removed_persistent": removed_persistent}


async def env_list() -> Dict[str, Any]:
    """列出环境变量（敏感键脱敏，长值截断）。"""
    items = []
    for k, v in sorted(os.environ.items()):
        display = "[REDACTED]" if k.upper() in _SENSITIVE_ENV_KEYS else str(v)
        if len(display) > _MAX_VALUE_LEN:
            display = display[:_MAX_VALUE_LEN] + "..."
        items.append({"name": k, "value": display})
    return {"count": len(items), "env": items}


# ---------------------------------------------------------------- 注册表

_REG_HIVES = {
    "HKCR": "HKEY_CLASSES_ROOT", "HKCU": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE", "HKU": "HKEY_USERS", "HKCC": "HKEY_CURRENT_CONFIG",
}

_TYPE_MAP = {  # winreg 常量名 → 模型可读名
    "REG_SZ": winreg.REG_SZ, "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
    "REG_DWORD": winreg.REG_DWORD, "REG_QWORD": winreg.REG_QWORD,
    "REG_BINARY": winreg.REG_BINARY, "REG_MULTI_SZ": winreg.REG_MULTI_SZ,
} if os.name == "nt" else {}


def _require_winreg():
    if os.name != "nt":
        raise RuntimeError("注册表操作仅支持 Windows")


def _parse_reg_path(path: str):
    """'HKCU\\Software\\Foo' → (winreg hive, subkey)。"""
    _require_winreg()
    parts = str(path).replace("/", "\\").split("\\", 1)
    hive_name = parts[0].upper()
    if hive_name not in _REG_HIVES:
        raise ValueError(f"无效注册表根键: {hive_name}（支持 HKCU/HKLM/HKCR/HKU/HKCC）")
    return getattr(winreg, _REG_HIVES[hive_name]), (parts[1] if len(parts) > 1 else "")


def _serialize_value(value: Any) -> Any:
    """winreg 返回值 → JSON 安全表示。"""
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, tuple):
        return [_serialize_value(v) for v in value]
    return value


def _parse_input_value(value: Any, reg_type: str) -> Any:
    """模型传入值 → winreg 类型。"""
    if reg_type in ("REG_DWORD", "REG_QWORD"):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"REG_{'Q' if reg_type == 'REG_QWORD' else 'D'}WORD 需要整数，收到: {value!r}")
    if reg_type == "REG_BINARY":
        if isinstance(value, str):
            value = value.removeprefix("0x")
            if not value or len(value) % 2:
                raise ValueError("REG_BINARY 需十六进制字符串（偶数长度，如 '0A0BFF'）")
            return bytes.fromhex(value)
        return bytes(value)
    if reg_type == "REG_MULTI_SZ":
        if isinstance(value, str):
            return [s for s in value.split("\n") if s != ""]
        return list(value)
    return str(value)


async def registry_read(path: str, name: str = "") -> Dict[str, Any]:
    """读取注册表值。name 为空时返回该键的默认值；键含子键时列出子键。"""
    try:
        hive, subkey = _parse_reg_path(path)
        with winreg.OpenKey(hive, subkey) as key:
            if name:
                value, vtype = winreg.QueryValueEx(key, str(name))
                return {"path": path, "name": str(name), "value": _serialize_value(value),
                        "type": _find_type_name(vtype), "status": "success"}
            # 无 name：默认值
            try:
                value, vtype = winreg.QueryValueEx(key, "")
                return {"path": path, "name": "(默认)", "value": _serialize_value(value),
                        "type": _find_type_name(vtype), "status": "success"}
            except FileNotFoundError:
                # 无默认值：列子键 + 值
                return await registry_list(path)
    except FileNotFoundError:
        return {"error": f"注册表路径不存在: {path}"}
    except Exception as e:
        return {"error": f"读取注册表失败: {e}"}


def _find_type_name(vtype: int) -> str:
    for name, const in _TYPE_MAP.items():
        if const == vtype:
            return name
    return f"REG_TYPE_{vtype}"


async def registry_list(path: str) -> Dict[str, Any]:
    """列出注册表键的子键和值。"""
    try:
        hive, subkey = _parse_reg_path(path)
        with winreg.OpenKey(hive, subkey) as key:
            subkeys, values = [], []
            idx = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, idx))
                    idx += 1
                except OSError:
                    break
            idx = 0
            while True:
                try:
                    vname, vvalue, vtype = winreg.EnumValue(key, idx)
                    display = _serialize_value(vvalue)
                    s = str(display)
                    if len(s) > _MAX_VALUE_LEN:
                        display = s[:_MAX_VALUE_LEN] + "..."
                    values.append({"name": vname or "(默认)", "value": display,
                                   "type": _find_type_name(vtype)})
                    idx += 1
                except OSError:
                    break
            return {"path": path, "subkeys": subkeys, "values": values,
                    "subkey_count": len(subkeys), "value_count": len(values), "status": "success"}
    except FileNotFoundError:
        return {"error": f"注册表路径不存在: {path}"}
    except Exception as e:
        return {"error": f"列出注册表失败: {e}"}


async def registry_write(path: str, name: str, value: Any,
                         type: str = "REG_SZ", create_key: bool = False) -> Dict[str, Any]:
    """写入注册表值。type 支持 REG_SZ/REG_EXPAND_SZ/REG_DWORD/REG_QWORD/REG_BINARY/REG_MULTI_SZ。"""
    reg_type = str(type or "REG_SZ").upper()
    if reg_type not in _TYPE_MAP:
        return {"error": f"不支持的注册表类型: {type}（支持: {', '.join(_TYPE_MAP)}）"}
    try:
        hive, subkey = _parse_reg_path(path)
        parsed = _parse_input_value(value, reg_type)
        access = winreg.KEY_SET_VALUE | (winreg.KEY_CREATE_SUB_KEY if create_key else 0)
        with winreg.CreateKeyEx(hive, subkey, 0, access) as key:
            winreg.SetValueEx(key, str(name or ""), 0, _TYPE_MAP[reg_type], parsed)
        return {"path": path, "name": str(name or "(默认)"), "value": _serialize_value(parsed),
                "type": reg_type, "status": "success"}
    except Exception as e:
        return {"error": f"写入注册表失败: {e}"}


async def registry_delete(path: str, name: str = "") -> Dict[str, Any]:
    """删除注册表值（name 为空删除默认值）。"""
    try:
        hive, subkey = _parse_reg_path(path)
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, str(name or ""))
            except FileNotFoundError:
                return {"error": f"注册表值不存在: {path}\\{name or '(默认)'}"}
        return {"path": path, "name": str(name or "(默认)"), "status": "success", "deleted": True}
    except Exception as e:
        return {"error": f"删除注册表值失败: {e}"}


# ---------------------------------------------------------------- 管理员运行

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32), ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


async def run_as_admin(executable: str, args: str = "", cwd: str = "",
                       wait: bool = False, wait_timeout: int = 120) -> Dict[str, Any]:
    """以管理员权限运行程序/命令（触发 UAC 确认弹窗）。

    executable: 程序路径或命令名（cmd/powershell/regedit/...）。
    args: 命令行参数；cwd: 工作目录；wait: 是否等待进程结束并返回退出码。
    """
    exe = str(executable or "").strip()
    if not exe:
        return {"error": "参数 executable 不能为空"}
    if os.name != "nt":
        return {"error": "管理员运行（UAC 提权）仅支持 Windows"}
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            # 当前非管理员 → ShellExecuteEx runas 弹 UAC
            sei = _SHELLEXECUTEINFOW()
            sei.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
            sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
            sei.lpVerb = "runas"
            sei.lpFile = exe
            sei.lpParameters = str(args or "")
            sei.lpDirectory = str(cwd or "") or None
            sei.nShow = 1  # SW_SHOWNORMAL
            ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
            if not ok:
                err = ctypes.windll.kernel32.GetLastError()
                if err == 1223:  # ERROR_CANCELLED：用户取消 UAC
                    return {"error": "用户取消了 UAC 提权确认", "cancelled": True}
                return {"error": f"ShellExecuteEx 失败，错误码 {err}"}
            pid = sei.hProcess
        else:
            # 当前已是管理员 → 直接启动（无需提权）
            import subprocess
            proc = subprocess.Popen([exe] + (str(args or "").split()),
                                    cwd=str(cwd) or None)
            pid = proc.pid if proc.pid else None

        result: Dict[str, Any] = {"status": "success", "elevated": True,
                                  "executable": exe, "args": str(args or "")}
        if wait and pid:
            # 等待进程退出（阻塞，丢线程池）
            def _wait_proc():
                WAIT_TIMEOUT = 0x00000102
                INFINITE = 0xFFFFFFFF
                timeout_ms = max(int(wait_timeout), 1) * 1000
                rc = ctypes.windll.kernel32.WaitForSingleObject(pid, timeout_ms)
                if rc == WAIT_TIMEOUT:
                    return {"waited": False, "error": f"等待超时（{wait_timeout}s）"}
                exit_code = ctypes.c_uint32()
                ctypes.windll.kernel32.GetExitCodeProcess(pid, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(pid)
                return {"waited": True, "exit_code": int(exit_code.value)}
            result.update(await asyncio.to_thread(_wait_proc))
        return result
    except Exception as e:
        return {"error": f"管理员运行失败: {e}"}
