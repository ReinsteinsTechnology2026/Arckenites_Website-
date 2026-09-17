"""
Windows Service wrapper around agent_core.LabAgentRunner. This is the only
file that imports pywin32 — kept separate from agent_core.py so the actual
enforcement logic can be unit-tested on a non-Windows-service-aware
environment.

UNVERIFIED ON REAL HARDWARE — see README.md. This has not been installed
or run as an actual Windows Service on a real lab VM yet.
"""

import logging
import os
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil

from agent_core import AgentConfig, LabAgentRunner

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.log")


def _configure_logging() -> None:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


class ArckenitesLabAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "ArckenitesLabAgent"
    _svc_display_name_ = "Arckenites VM Lab Agent"
    _svc_description_ = (
        "Periodically checks this VM's authorized student with the "
        "Arckenites Hub and enforces RDP access locally. Never disables "
        "RDP globally and never touches the Administrator account."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.runner: LabAgentRunner | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if self.runner:
            self.runner.stop_requested = True

    def SvcDoRun(self):
        _configure_logging()
        logger = logging.getLogger("arckenites_lab_agent")
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        try:
            config = AgentConfig.from_file(CONFIG_PATH)
        except Exception:
            logger.exception("Failed to load config.ini — service cannot start")
            servicemanager.LogErrorMsg("Arckenites Lab Agent: failed to load config.ini, see agent.log")
            return

        self.runner = LabAgentRunner(config)
        logger.info("Arckenites Lab Agent starting, polling every %ss", config.poll_interval_sec)

        while True:
            self.runner.run_once()
            # Wait on the stop event instead of a plain sleep so SvcStop()
            # can interrupt the poll interval immediately instead of
            # leaving the service unresponsive to `net stop` for up to a
            # full poll_interval_sec.
            rc = win32event.WaitForSingleObject(self.stop_event, config.poll_interval_sec * 1000)
            if rc == win32event.WAIT_OBJECT_0:
                break

        logger.info("Arckenites Lab Agent stopped")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ArckenitesLabAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(ArckenitesLabAgentService)
