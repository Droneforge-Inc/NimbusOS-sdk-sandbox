import math

from nimbus_developer import NimbusClient


def main() -> None:
    with NimbusClient() as client:
        for message in client.subscribe_telemetry():
            telemetry = message.decoded

            fields = [
                f"seq={message.seq}",
                f"t_ns={message.t_ns}",
            ]

            battery = telemetry.Battery()
            if battery is not None:
                fields.append(f"voltage={battery.Voltage():.2f}V")
                fields.append(f"current={battery.Current():.2f}A")
                fields.append(f"remaining={battery.RemainingCapacity():.0f}mAh")

            attitude = telemetry.Attitude()
            if attitude is not None:
                fields.append(f"roll={math.degrees(attitude.Roll()):.1f}deg")
                fields.append(f"pitch={math.degrees(attitude.Pitch()):.1f}deg")
                fields.append(f"yaw={math.degrees(attitude.Yaw()):.1f}deg")

            link = telemetry.LinkStats()
            if link is not None:
                rf_mode = link.RfMode()
                fields.append(f"uplink_lq={link.UplinkLinkQuality():.0f}")
                fields.append(
                    f"rf_mode={rf_mode.decode('utf-8') if rf_mode is not None else 'unknown'}"
                )

            print(" ".join(fields), flush=True)


if __name__ == "__main__":
    main()
