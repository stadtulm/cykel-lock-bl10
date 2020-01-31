from crc_itu import crc16
from construct import *
from construct.lib import *


class PacketParser:
    login = Struct(
        "imei" / Hex(Bytes(8)),
        "model" / Hex(Bytes(2)),
        "tzlg" / BitStruct(
            "tz" / BitsInteger(12),
            "gmt" / Enum(Bit, eastern=0, western=1),
            Padding(1),
            "lang" / BitsInteger(2)
        )
    )

    heartbeat = Struct(
        "tic" / BitStruct(
            Padding(1),
            "gps" / Flag,
            Padding(3),
            "charge" / Flag,
            Padding(1),
            "locked" / Flag # TEST
        ),
        "voltage" / Bytes(2),
        "signal" / Enum(Byte, none=0x00, extemely_weak=0x01, weak=0x02, good=0x03, strong=0x04),
        "extportstatus" / Byte,
        "language" / Enum(Byte, chinese=0x01, english=0x02)
    )

    response = Struct(
        "length" / Bytes(4),
        "encoding" / Enum(Byte, ascii=0x01, utf16be=0x02),
        "content" / GreedyBytes
    )

    gps = Struct(
        "gps_satellites" / Byte,
        "latitude" / Bytes(4),
        "longitude" / Bytes(4),
        "speed" / Byte,
        "cs" / BitStruct(
            Padding(2),
            "gps_rtdp" / Enum(Bit, realtime=0, differential=1),
            "positioning" / Flag,
            "longitude" / Enum(Bit, east=0, west=1),
            "latitude" / Enum(Bit, south=0, north=1),
            "course" / BitsInteger(10)
        )
    )

    main_lbs = Struct(
        "mcc" / Bytes(2),
        "mnc" / Byte,
        "lac" / Bytes(2),
        "ci" / Bytes(3),
        "rssi" / Byte,
    )

    lbs = Struct(
        "lac" / Bytes(2),
        "ci" / Bytes(3),
        "rssi" / Byte,
    )

    wifi = Struct(
        "mac" / Bytes(6),
        "strength" / Byte,
    )

    reserved = Struct(
        "bluetoothflag" / Bytes(2),
        "reupload" / Flag
    )

    location = Struct(
        "datetime" / Bytes(6),
        "gps_length" / Byte,
        "gps" / If(this.gps_length == 12, gps),
        "main_lbs_length" / Byte,
        "main_lbs" / If(this.main_lbs_length == 9, main_lbs),
        "lbs_sub_length" / Byte,
        "lbs" / Array(lambda ctx: int(int(ctx.lbs_sub_length) / 6), lbs),
        "wifi_length" / Byte,
        "wifi" / Array(lambda ctx: int(int(ctx.wifi_length) / 7), wifi),
        "status" / Byte, # FIXME enum
        "reserved_length" / Byte,
        "reserved" / If(this.reserved_length == 3, reserved)
    )

    info = Struct(
        "type" / Enum(Byte, imei=0x00, imsi=0x01, iccid=0x02, chipid=0x03, bluetoothmac=0x04, unlockkey=0x05, fwversion=0x07, default=Pass),
        "length" / BytesInteger(2),
        "content" / Bytes(this.length)
    )

    information = GreedyRange(info) #RepeatUntil(lambda obj,lst,ctx: something_current_position, (ctx._.length - 1 - 2 - 2), info)

    protocol = Struct(
        "start" / OneOf(Bytes(2), [b"\x78\x78", b"\x79\x79"]),
        "length" / IfThenElse(this.start == b"\x78\x78", Int8ub, BytesInteger(2)),
        "protocol" / Enum(Byte, login=0x01, heartbeat=0x23, response=0x21, location=0x32, alarm=0x33, command=0x80, information=0x98, default=Pass),
        "data" / Switch(this.protocol,
            {
                "login": login,
                "heartbeat": heartbeat,
                "response": response,
                "location": location,
                "alarm": location,
                "information": information,
            },
            default=Bytes(this.length - 1 - 2 - 2)
        ),
        "serial" / Bytes(2),
        "crc" / Bytes(2),
        #Checksum(Bytes(2),
        #lambda data: crc16(data),
        #lambda s: bytes([s.length]) + bytes([int(s.protocol)]) + s.data + s.serial
        #),
        "end" / Const(b"\x0d\x0a")
    )

    #def __init__(self):
    #    # void

    def parse(self, packet):
        return self.protocol.parse(packet)