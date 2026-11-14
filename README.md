# Cubes Monitor

A free, local Windows system monitor built with Python, Tkinter, and psutil.

## Features

- Live CPU utilization, processor model, and frequency
- RAM usage and availability
- GPU name and best-effort GPU utilization (Windows performance counters; may be unavailable on some drivers)
- System drive capacity and usage
- Network download/upload rates
- Disk read/write throughput
- Top processes by CPU and memory
- Live CPU/RAM history graph
- Standard-user operation; no account, telemetry, or paid service

## Requirements

- Windows 10/11
- Python 3.10+
- Tkinter (included with standard Windows Python installers)
- Dependencies in `requirements.txt`

## Run from source

```powershell
py -m pip install -r requirements.txt
py main.py
```

## CubesEPA packaging

Package this folder with your installed CubesEPA CLI. The ordinary build should not request elevation:

```powershell
cubes create . -o CubesMonitor.epa --name "Cubes Monitor" --publisher "Cubes, Studio." --app-version 1.0.0 --description "Local Windows system monitor"
```

If a separate elevated build is useful for inspecting protected process details, build it with the CubesEPA administrator option:

```powershell
cubes create . -o CubesMonitor-Admin.epa --name "Cubes Monitor (Administrator)" --publisher "Cubes, Studio." --app-version 1.0.0 --description "Local Windows system monitor" --require-admin
```

The elevated archive should use CubesEPA's normal safety warning and Windows UAC consent flow. The application itself does not attempt to bypass UAC or silently elevate. Most metrics work without administrator privileges.

## Limitations

- GPU utilization depends on Windows GPU Engine counters and the installed graphics driver. The GPU name can still be shown when utilization is unavailable.
- Windows may restrict details for protected processes, especially when running as a standard user.
- Network rates are system-wide totals, not per-process traffic.
