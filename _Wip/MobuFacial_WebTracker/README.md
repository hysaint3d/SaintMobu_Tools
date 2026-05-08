# MobuWebTracker

A zero-install browser-based tracking tool for MotionBuilder.

## Setup Instructions

1.  **Start the Bridge**:
    - **Recommended**: Run `start_GUI_bridge.bat` to open the graphical interface.
    - Inside the GUI, you can:
        - Switch between **VMC** (MoBu) and **OSC** (Generic) modes.
        - Customize Target IP and Port.
        - Monitor connection status and packet logs.
    - *Alternative*: Use the individual `.bat` files for CLI versions.

2.  **Open the Tracker**:
    - Open `MobuFacial_WebTracker.html` in **Google Chrome** or **Microsoft Edge**.
    - Wait for "MediaPipe: Ready" (green status).
    - Click **Start Camera & Tracking**.

3.  **Setup Target App**:
    - **For MoBu**: Run the `VMC2Mobu_MultiActor.py` script and click Connect.
    - **For OSC Apps**: Configure your app to listen on the target port (e.g., 9000).

## Troubleshooting
- **No data in MoBu**: Check if the Bridge console says "Browser connected!". If not, refresh the browser page.
- **Firewall**: Ensure port 8080 (WS) and 39539 (UDP) are allowed on your local network if using separate devices.
- **HTTPS**: If running from a remote server, Chrome requires HTTPS for camera access. For local files (`file:///`), it works fine.
