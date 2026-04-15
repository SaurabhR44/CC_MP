"""
Process isolation primitives.

Implements container isolation using Linux primitives:
- fork() - create child process
- chroot() - change root filesystem
- unshare() - (optional) detach namespaces
- exec() - replace process with command
"""

import os
import sys
import subprocess
import select
from pathlib import Path
from typing import List, Dict, Optional, Tuple


def isolate_and_exec(
    container_root: Path,
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
    capture_output: bool = False,
) -> Tuple[int, str, str]:
    """
    Execute a command in an isolated container.
    
    If capture_output is True, returns (exit_code, stdout, stderr).
    Otherwise returns (exit_code, "", "").
    """
    
    if not _is_linux() or os.geteuid() != 0:
        if not capture_output:
            return _exec_without_isolation(container_root, working_dir, env_vars, cmd), "", ""
        else:
            return _exec_without_isolation_capture(container_root, working_dir, env_vars, cmd)

    # Prepare pipes for output capture
    stdout_pipe_r, stdout_pipe_w = os.pipe() if capture_output else (None, None)
    stderr_pipe_r, stderr_pipe_w = os.pipe() if capture_output else (None, None)

    child_pid = os.fork()
    
    if child_pid == 0:
        # --- Child Process ---
        try:
            if capture_output:
                os.close(stdout_pipe_r)
                os.close(stderr_pipe_r)
                os.dup2(stdout_pipe_w, sys.stdout.fileno())
                os.dup2(stderr_pipe_w, sys.stderr.fileno())

            # Isolate
            os.chroot(str(container_root))
            os.chdir(working_dir)
            
            # Environment
            os.environ.clear()
            os.environ.update(env_vars)
            
            # Execute
            os.execvp(cmd[0], cmd)
        except Exception as e:
            print(f"Container Error: {e}", file=sys.stderr)
            os._exit(1)
    else:
        # --- Parent Process ---
        if capture_output:
            os.close(stdout_pipe_w)
            os.close(stderr_pipe_w)

        stdout_data = []
        stderr_data = []
        
        if capture_output:
            readers = [stdout_pipe_r, stderr_pipe_r]
            while readers:
                readable, _, _ = select.select(readers, [], [])
                for fd in readable:
                    data = os.read(fd, 4096)
                    if not data:
                        readers.remove(fd)
                        os.close(fd)
                        continue
                    if fd == stdout_pipe_r:
                        stdout_data.append(data.decode('utf-8', errors='replace'))
                    else:
                        stderr_data.append(data.decode('utf-8', errors='replace'))

        _, status = os.waitpid(child_pid, 0)
        exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        
        return exit_code, "".join(stdout_data), "".join(stderr_data)


def _is_linux() -> bool:
    return sys.platform.startswith('linux')


def _exec_without_isolation(
    container_root: Path,
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
) -> int:
    """Fallback for non-root/non-Linux."""
    resolved_cwd = container_root / working_dir.lstrip('/')
    if not resolved_cwd.exists():
        resolved_cwd = container_root
        
    env = os.environ.copy()
    env.update(env_vars)
    
    try:
        result = subprocess.run(cmd, cwd=str(resolved_cwd), env=env)
        return result.returncode
    except Exception as e:
        print(f"Execution failed: {e}", file=sys.stderr)
        return 1


def _exec_without_isolation_capture(
    container_root: Path,
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
) -> Tuple[int, str, str]:
    """Fallback with capture."""
    resolved_cwd = container_root / working_dir.lstrip('/')
    if not resolved_cwd.exists():
        resolved_cwd = container_root

    env = os.environ.copy()
    env.update(env_vars)

    try:
        result = subprocess.run(
            cmd, 
            cwd=str(resolved_cwd), 
            env=env, 
            capture_output=True, 
            text=True
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)

