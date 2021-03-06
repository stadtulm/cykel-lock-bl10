import os
import binascii
import requests
import jsons
import time
from datetime import datetime, timezone
from prometheus_client import Gauge
from prometheus_client.twisted import MetricsResource
from twisted.internet import protocol, reactor, endpoints
from twisted.protocols.basic import LineReceiver
from twisted.web.server import Site
from klein import Klein
from packet import Packet, DEFAULT_CRC_SECRET

HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', '8000'))
LOCK_HOST = os.environ.get('LOCK_HOST', HOST)
LOCK_PORT = int(os.environ.get('LOCK_PORT', '21105'))
ENDPOINT = os.environ['ENDPOINT']
ENDPOINT_AUTH_HEADER = os.getenv('ENDPOINT_AUTH_HEADER', '')
LABELS = os.getenv('LABELS', None)
CRC_SECRET = os.getenv('CRC_SECRET', DEFAULT_CRC_SECRET)

headers = {
    'Content-Type': 'application/json'
}
if ENDPOINT_AUTH_HEADER is not '':
    headers['Authorization'] = ENDPOINT_AUTH_HEADER

promlabels = {}
if LABELS is not None:
    promlabels = dict(s.split('=') for s in LABELS.split(','))

trackervoltgauge = Gauge('tracker_battery_volts', 'tracker battery voltage', ['device_id'] + list(promlabels.keys()))
lockvoltgauge = Gauge('lock_battery_volts', 'lock battery voltage', ['device_id'] + list(promlabels.keys()))
trackertimegauge = Gauge('tracker_last_data_update', 'tracker last data timestamp', ['device_id'] + list(promlabels.keys()))
locktimegauge = Gauge('lock_last_data_update', 'lock last data timestamp', ['device_id'] + list(promlabels.keys()))

devices = dict()

# FIXME handle connection close
class BL10(LineReceiver):
    device_id = None
    serial = 0

    def __init__(self):
        self.packet = Packet(crc_secret=CRC_SECRET)

    def printPacket(self, direction, packet):
        dt = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone().replace(microsecond=0).isoformat()
        if direction == '>':
            direction = '==>'
        else:
            direction = '<=='
        print("%s [%s] %s %s" % (dt, self.device_id, direction, binascii.hexlify(packet), ))

    def lineReceived(self, line):
        self.printPacket("<", line)

        lbl = { 'device_id': self.device_id, **promlabels }
        locktimegauge.labels(**lbl).set(int(time.time()))

        try:
            data = self.packet.parse(line + b'\r\n')
            print(data)
            proto = str(data.protocol)
            print(proto)
            self.serial = data.serial
            if proto == 'login':
                self.handleLogin(data)
            elif proto == 'heartbeat':
                self.handleHeartbeat(data)
            elif proto == 'location':
                self.handleLocation(data)
            elif proto == 'alarm':
                self.handleAlarm(data)
            elif proto == 'information':
                self.handleInformation(data)
            elif proto == 'response':
                self.handleResponse(data)
            else:
                self.handleUnknown(data)
        except Exception as e:
            print(e)
            pass

    def write(self, data):
        self.printPacket(">", data)
        self.transport.write(data)

    def handleLogin(self, data):
        print("login from %s (%s)" % (data.data.imei, data.data.model))

        now = datetime.now(timezone.utc)
        year = int(now.strftime("%y"))
        dt = dict(year=year, month=now.month, day=now.day, hour=now.hour, minute=now.minute, second=now.second)
        respdata = Packet.login_response.build(dict(datetime=dt, reserved_length=0, reserved=0))
        self.serial += 1
        resp = self.packet.build(dict(start=b"\x78\x78", fields=dict(value=dict(length=1+(6+1+0)+2+2, protocol=0x01, data=respdata, serial=self.serial))))
        self.write(resp)

        self.device_id = str(data.data.imei)
        devices[self.device_id] = self

        update = {
            'device_id': self.device_id
        }
        print(jsons.dumps(update))
        resp = requests.post(ENDPOINT, headers=headers, data=jsons.dumps(update))
        print(resp)
        print(resp.text)

    def handleHeartbeat(self, data):
        self.serial += 1
        resp = self.packet.build(dict(start=b"\x78\x78", fields=dict(value=dict(length=1+2+2, protocol=0x23, data=bytes(), serial=self.serial))))
        self.write(resp)

        lbl = { 'device_id': self.device_id, **promlabels }
        trackervoltgauge.labels(**lbl).set(data.data.voltage)
        lockvoltgauge.labels(**lbl).set(data.data.voltage)

        update = {
            'device_id': self.device_id,
            'battery_voltage': data.data.voltage
        }
        print(jsons.dumps(update))
        resp = requests.post(ENDPOINT, headers=headers, data=jsons.dumps(update))
        print(resp)
        print(resp.text)

    def handleLocation(self, data):
        self.serial += 1
        resp = self.packet.build(dict(start=b"\x79\x79", fields=dict(value=dict(length=1+2+2, protocol=0x32, data=bytes(), serial=self.serial))))
        self.write(resp)
        self.submitLocation(data)

    def submitLocation(self, data):
        lbl = { 'device_id': self.device_id, **promlabels }
        trackertimegauge.labels(**lbl).set(int(time.time()))

        update = {
            'device_id': self.device_id
        }

        if data.data.gps:
            # FIXME: something something with the data.data.gps.cs.latitude, data.data.gps.cs.longitude flags
            update['lat'] = data.data.gps.latitude
            update['lng'] = data.data.gps.longitude

        print(jsons.dumps(update))
        resp = requests.post(ENDPOINT, headers=headers, data=jsons.dumps(update))
        print(resp)
        print(resp.text)

    def handleAlarm(self, data):
        self.serial += 1
        resp = self.packet.build(dict(start=b"\x79\x79", fields=dict(value=dict(length=1+2+2, protocol=0x33, data=bytes(), serial=self.serial))))
        self.write(resp)
        self.submitLocation(data)

    def handleInformation(self, data):
        self.serial += 1
        resp = self.packet.build(dict(start=b"\x79\x79", fields=dict(value=dict(length=1+(1)+2+2, protocol=0x98, data=bytes(1), serial=self.serial))))
        self.write(resp)

    def handleResponse(self, data):
        self.serial += 1
        print("Got response: %s" % (data.data.content,))

    def handleUnknown(self, data):
        self.serial += 1
        print("Got unkown packet, protocol is %d" % (data.protocol,))

    def sendCommand(self, command):
        length = len(command)
        self.serial += 1
        data = Packet.command.build(dict(length=4+length, serverflag=0, content=command))
        resp = self.packet.build(dict(start=b"\x79\x79", fields=dict(value=dict(length=1+(1+4+length)+2+2, protocol=0x80, data=data, serial=self.serial))))
        self.write(resp)

    def sendUnlock(self):
        self.sendCommand(b"UNLOCK#")

    def ring(self):
        self.sendCommand(b"SDFIND,ON,3,15,1#")

    def locate(self):
        self.sendCommand(b"LJDW#")

class BL10Factory(protocol.Factory):
    def buildProtocol(self, addr):
        return BL10()

http = Klein()

class NotFound(Exception):
    pass

@http.handle_errors(NotFound)
def not_found(request, failure):
    request.setResponseCode(404)
    return 'Not found'

@http.route('/')
def home(request):
    return 'Hello world!'

@http.route('/metrics')
def metrics(request):
    return MetricsResource()

@http.route('/list')
def list(request):
    return ','.join(devices.keys())

@http.route('/<imei>/unlock', methods=['POST'])
def lock_open(request, imei):
    print("unlock: %s" % (imei,))
    dev = devices.get(imei)
    if dev is None:
        raise NotFound()
    request.setHeader('Content-Type', 'application/json')
    dev.sendUnlock()
    # FIXME: async, get confirmation from lock
    data = {"success": True, "status": "pending"}
    print(jsons.dumps(data))
    return jsons.dumps(data)

@http.route('/<imei>')
def lock(request, imei):
    dev = devices.get(imei)
    if dev is None:
        raise NotFound()
    return 'Hi %s!' % (imei,)

bl10endpoint = endpoints.TCP4ServerEndpoint(reactor, LOCK_PORT, interface=LOCK_HOST)
bl10endpoint.listen(BL10Factory())
print("Listening for Lock Traffic on %s:%d" % (LOCK_HOST, LOCK_PORT))

httpendpoint = endpoints.TCP4ServerEndpoint(reactor, PORT, interface=HOST)
httpendpoint.listen(Site(http.resource()))
print("Listening for HTTP on %s:%d" % (HOST, PORT))

reactor.run()