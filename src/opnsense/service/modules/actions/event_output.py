
import os
import subprocess
import selectors
import traceback
import signal
from .. import syslog_error, syslog_info
from .base import BaseAction

class Action(BaseAction):
    selector = None
    registered_events = set()
    processes = None
    fd_buffers = None

    def execute(self, parameters, message_uuid, connection):
        super().execute(parameters, message_uuid, connection)
        try:
            script_command = self._cmd_builder(parameters)
        except TypeError as e:
            return str(e)
        
        if self.event is None:
            syslog_error('[%s] No event type specified' % message_uuid)
            return 'Execute error'
        
        def cleanup(event = None):
            if event is None:
                Action.selector.close()
                Action.registered_events = set()
                for _, p in Action.processes.items():
                    p.kill()
                    # kill child processes as well
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            else:
                Action.selector.unregister(Action.processes[event].stdout)
                Action.selector.unregister(Action.processes[event].stderr)
                Action.processes[event].kill()
                # kill child processes as well
                try:
                    os.killpg(os.getpgid(Action.processes[event].pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                del Action.processes[event]
                if event in Action.fd_buffers:
                    del Action.fd_buffers[event]
                Action.registered_events.remove(event)
        
        def handle_events(key, mask):
            name = key.data.split("_")
            event_name = name[0]
            type = name[1]
            if type == "stderr":
                return_code = Action.processes[event_name].wait()
                err = Action.processes[event_name].stderr.read().decode()
                if len(err) > 0:
                    syslog_error('[%s] Script action stderr returned "%s" (%d)' % (
                        message_uuid, err.strip()[:255], return_code
                    ))
                cleanup(event_name)
                return

            data = key.fileobj.read(1024)
            if data:
                if event_name not in Action.fd_buffers:
                    Action.fd_buffers[event_name] = b""
                Action.fd_buffers[event_name] += data

                if b"\n" in Action.fd_buffers[event_name]:
                    line = Action.fd_buffers[event_name].split(b"\n")[0]
                    formatted_data = f"event: {event_name}\ndata: {line.decode().strip()}\n\n"
                    connection.send(formatted_data.encode())
                    
                    del Action.fd_buffers[event_name]
            else:
                # EOF - child process terminated
                cleanup(event_name)

        def spawn_and_register(command, event):
            if (event in Action.registered_events):
                # close action
                cleanup(event)
                return

            process = subprocess.Popen(
                command,
                env=self.config_environment,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                preexec_fn=os.setsid
            )

            Action.selector.register(process.stdout, selectors.EVENT_READ, data=f"{event}_stdout")
            Action.selector.register(process.stderr, selectors.EVENT_READ, data=f"{event}_stderr")
            Action.registered_events.add(event)
            Action.processes[event] = process

        if len(Action.registered_events) == 0:
            Action.selector = selectors.DefaultSelector()
            Action.processes = {}
            Action.fd_buffers = {}
            try:
                spawn_and_register(script_command, self.event)
                while True:
                    timeout = True
                    for key, mask in Action.selector.select(1):
                        timeout = False
                        handle_events(key, mask)
                    if timeout:
                        try:
                            connection.getpeername()
                        except OSError:
                            syslog_info('[%s] Script action terminated by other end' % message_uuid)
                            break
                    if not Action.selector.get_map():
                        # no events left to monitor
                        break
            except Exception as script_exception:
                syslog_error('[%s] Script action failed with %s at %s' % (message_uuid, script_exception, traceback.format_exc()))
                return 'Execute error'
            finally:
                cleanup()
        else:
            # event loop already running
            spawn_and_register(script_command, self.event)
