from nimbusos_sdk import NimbusClient
from nimbusos_sdk import ReceivedMessage


def main() -> None:
    print("import ok")
    print(f"NimbusClient={NimbusClient.__name__}")
    print(f"ReceivedMessage={ReceivedMessage.__name__}")


if __name__ == "__main__":
    main()
