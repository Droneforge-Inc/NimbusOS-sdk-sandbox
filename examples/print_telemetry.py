from nimbusos_sdk import NimbusClient


def main() -> None:
    with NimbusClient() as client:
        for telemetry in client.telemetry():
            print(
                telemetry.seq,
                telemetry.battery.voltage,
                telemetry.battery.current,
                telemetry.attitude.roll_deg,
                telemetry.attitude.pitch_deg,
                telemetry.attitude.yaw_deg,
                telemetry.link.uplink_link_quality,
                telemetry.link.rf_mode,
                flush=True,
            )


if __name__ == "__main__":
    main()
