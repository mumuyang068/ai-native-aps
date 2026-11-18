"""Task-local, explicit-file source gate. No directory tree copying or SQL sources."""
import os
from pathlib import Path
import re
import sys
import stat
from urllib.parse import unquote, urlsplit


def sql_path(path):
    name = Path(path).name.lower()
    return name.endswith(('.db', '.sqlite', '.sqlite3', '-wal', '-shm', '.wal', '.shm', '-journal'))


def unlinked(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            reparse = bool(getattr(part.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            reparse = False
        if part.is_symlink() or reparse:
            raise PermissionError('LINK_SOURCE_OR_DESTINATION: '+str(path))
    resolved = path.resolve()
    # pathlib preserves the extended namespace on Windows. pytest/shutil use
    # that spelling for long task-local paths; it must compare as the same
    # physical location, without permitting device namespaces or link escapes.
    value = str(resolved)
    if os.name == 'nt' and value.startswith('\\\\?\\'):
        if value[4:8].upper() == 'UNC\\':
            resolved = Path('\\\\' + value[8:])
        elif len(value) > 6 and value[4].isalpha() and value[5:7] == ':\\':
            resolved = Path(value[4:])
        else:
            raise PermissionError('UNSUPPORTED_DEVICE_PATH: '+value)
    return resolved


def _audit_path(value):
    """Resolve SQLite file URI spelling before applying the same root boundary.

    Queries such as mode=ro do not form part of the filesystem name. Decode
    once, reject remote/ambiguous URI forms, then use the existing link and
    Windows namespace checks. No files are opened to establish this identity.
    """
    if not isinstance(value,(str,bytes,os.PathLike)):
        return None
    value=os.fsdecode(value)
    if value.startswith('file:'):
        try:
            uri=urlsplit(value)
            if uri.netloc not in ('','localhost'):
                raise PermissionError('PROTECTED_SQL_URI_AUTHORITY')
            value=unquote(uri.path,errors='strict')
            if (not value or uri.fragment or '\x00' in value
                or value.replace('\\','/').startswith('//') or re.search(r'%[a-fA-F0-9]{2}',value)):
                raise PermissionError('PROTECTED_SQL_URI_PATH')
            if os.name=='nt' and len(value)>2 and value[0]=='/' and value[2]==':':
                value=value[1:]
        except (ValueError,UnicodeError):
            raise PermissionError('PROTECTED_SQL_URI_PATH') from None
    return unlinked(value)


def safe_source(path):
    # Test the name before any content access, including fake sentinel sources.
    if sql_path(path) or 'state' in [p.lower() for p in Path(path).parts]:
        raise PermissionError('PROTECTED_SOURCE: '+str(path))
    normalized = unlinked(path)
    if sql_path(normalized) or 'state' in [p.lower() for p in normalized.parts]:
        raise PermissionError('PROTECTED_SOURCE: '+str(normalized))
    return normalized


class SourceGuard:
    def __init__(self, files, destination):
        self.files = frozenset(safe_source(p) for p in files)
        self.destination = unlinked(destination)

    def source(self, path):
        path = safe_source(path)
        if path not in self.files or not path.is_file():
            raise PermissionError('SOURCE_NOT_ALLOWLISTED_FILE: '+str(path))
        return path

    def target(self, path):
        path = unlinked(path)
        if not path.is_relative_to(self.destination) or path == self.destination:
            raise PermissionError('DESTINATION_ESCAPE: '+str(path))
        if sql_path(path):
            raise PermissionError('SQL_COPY_FORBIDDEN: '+str(path))
        return path

    def read_bytes(self, path):
        return self.source(path).read_bytes()

    def copy_file(self, source, destination):
        source, destination = self.source(source), self.target(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())


def install_sql_audit(task_root):
    """Deny SQL file opens/connections outside the fresh task root in this process."""
    root = unlinked(task_root)
    def audit(event, args):
        if event not in ('open', 'sqlite3.connect') or not args:
            return
        path = args[0]
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        path = os.fsdecode(path)
        if event == 'sqlite3.connect' and path == ':memory:':
            return
        if sql_path(path) or event == 'sqlite3.connect':
            # No content reads are used to establish the boundary.
            candidate = _audit_path(path)
            if not candidate.is_relative_to(root):
                raise PermissionError('PROTECTED_SQL: '+str(candidate))
    sys.addaudithook(audit)


def install_access_audit(task_root, *, read_files=(), code_roots=(), subprocess_policy=None):
    """Enforce task-local writes and explicit external records before file access.

    Code/runtime roots permit imports, never historical SQL or state access.
    External trees cannot be copied even if a caller supplies a trusted code root.
    Install in each service/test subprocess before importing application Owners.
    """
    root = unlinked(task_root)
    records = frozenset(safe_source(p) for p in read_files)
    if any(not p.is_file() for p in records):
        raise PermissionError('ALLOWLIST_REQUIRES_FILE')
    code = tuple(unlinked(p) for p in code_roots)
    null_device = unlinked(os.devnull)
    denied = []

    def path_of(value):
        return _audit_path(value)

    def refuse(reason, path):
        denied.append({'reason': reason, 'path': str(path)})
        raise PermissionError(reason+': '+str(path))

    def inside(path):
        return path == root or path.is_relative_to(root)

    def reading(path, *, sql=False):
        if inside(path):
            return
        if sql or sql_path(path):
            refuse('PROTECTED_SQL', path)
        if 'state' in [p.lower() for p in path.parts]:
            refuse('PROTECTED_STATE', path)
        if path in records:
            return
        if any(path == p or path.is_relative_to(p) for p in code):
            return
        refuse('READ_NOT_ALLOWLISTED', path)

    def writing(path):
        if not inside(path):
            refuse('WRITE_OUTSIDE_TASK', path)

    def audit(event, args):
        if not args:
            return
        if event=='subprocess.Popen' and subprocess_policy is not None and subprocess_policy(args):
            return
        if event in ('subprocess.Popen','os.system','os.exec','os.posix_spawn','os.spawn','os.startfile','os.startfile/2'):
            refuse('SUBPROCESS_NOT_AUTHORIZED', args[0])
        if event == 'sqlite3.connect':
            if args[0] == ':memory:':
                return
            path = path_of(args[0])
            if path is not None:
                reading(path, sql=True)
        elif event == 'open':
            path = path_of(args[0])
            if path is None or path == null_device:
                return
            mode = args[1] if len(args) > 1 else 'r'
            flags = args[2] if len(args) > 2 else 0
            write = (isinstance(mode, str) and any(m in mode for m in 'wax+')) or bool(
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if write:
                writing(path)
            else:
                reading(path)
        elif event == 'shutil.copytree':
            source, target = path_of(args[0]), path_of(args[1])
            if source is not None and not inside(source):
                refuse('TREE_SOURCE_FORBIDDEN', source)
            if target is not None:
                writing(target)
        elif event in ('os.scandir', 'os.listdir'):
            path = path_of(args[0])
            if path is not None:
                reading(path)
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime', 'os.truncate'):
            path = path_of(args[0])
            if path is not None:
                writing(path)
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            for value in args[:2]:
                path = path_of(value)
                if path is not None:
                    writing(path)

    sys.addaudithook(audit)
    return denied
