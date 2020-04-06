import asyncio, json, os, platform, sys, av, time, PIL, io
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration, AudioStreamTrack, VideoStreamTrack
from aiohttp_basicauth import BasicAuthMiddleware

VIDEO_DEVICE = '/dev/video0'
VIDEO_QUALITY = 50
MODE = 'dev'

class CameraDevice():
    def __init__(self):
        if sys.platform is 'win32':
            self.container = av.open(VIDEO_DEVICE, format='dshow')
        elif sys.platform is 'Linux':
            self.container = av.open(VIDEO_DEVICE, format='4fl2')
        elif sys.platform is 'osx':
            self.container = av.open(VIDEO_DEVICE, format='avfoundation')
        else:
            self.container = av.open(VIDEO_DEVICE)

        self.hasAudio = True
        
        if self.container.streams.video.__len__() == 0:
            print("Failed to open video stream. Exiting...")
            sys.exit()
        else:
            self.video = self.container.streams.get(video=0)[0]

        if self.container.streams.audio.__len__() == 0:
            self.hasAudio = False
            print("Stream contains no audio.")
        else:
            self.audio = self.container.streams.get(audio=0)[0]

    def rotate(self, frame):
        if flip:
            img = frame.to_image().convert(mode='RGB')
            frame = img.rotate(180)
        return frame

    async def get_next_frame(self):
        #for packet in self.container.demux(self.container.streams.video[0]):
        #    if packet.dts is None:
        #        continue
        for frame in self.container.decode(self.video):
            frame.pts = None
            return self.rotate(frame)

    async def get_jpeg_frame(self):
        frame = await self.get_next_frame()
        img = frame.to_image()
        with io.BytesIO() as output:
            img.save(output, format='JPEG', quality=VIDEO_QUALITY)
            contents = output.getvalue()
        return contents

class PeerConnectionFactory():
    def __init__(self):
        self.config = {'sdpSemantics': 'unified-plan'}
        self.STUN_SERVER = None
        self.TURN_SERVER = None
        self.TURN_USERNAME = None
        self.TURN_PASSWORD = None
        if all(k in os.environ for k in ('STUN_SERVER', 'TURN_SERVER', 'TURN_USERNAME', 'TURN_PASSWORD')):
            print('WebRTC connections will use your custom ICE Servers (STUN / TURN).')
            self.STUN_SERVER = os.environ['STUN_SERVER']
            self.TURN_SERVER = os.environ['TURN_SERVER']
            self.TURN_USERNAME = os.environ['TURN_USERNAME']
            self.TURN_PASSWORD = os.environ['TURN_PASSWORD']
            iceServers = [
                {
                    'urls': self.STUN_SERVER
                },
                {
                    'urls': self.TURN_SERVER,
                    'credential': self.TURN_PASSWORD,
                    'username': self.TURN_USERNAME
                }
            ]
            self.config['iceServers'] = iceServers

    def create_peer_connection(self):
        if self.TURN_SERVER is not None:
            iceServers = []
            iceServers.append(RTCIceServer(self.STUN_SERVER))
            iceServers.append(RTCIceServer(self.TURN_SERVER, username=self.TURN_USERNAME, credential=self.TURN_PASSWORD))
            return RTCPeerConnection(RTCConfiguration(iceServers))
        return RTCPeerConnection()

    def get_ice_config(self):
        return json.dumps(self.config)

class RTCVideoStream(VideoStreamTrack):
    def __init__(self, camera_device):
        super().__init__()
        self.camera_device = camera_device
        self.data_bgr = None

    async def recv(self):
        self.data_bgr = await self.camera_device.get_next_frame()
        frame = av.VideoFrame.from_ndarray(self.data_bgr, format='bgr24')
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame

class RTCAudioStream(AudioStreamTrack):
    def __init__(self, camera_device):
        super().__init__()
        self.camera_device = camera_device
        self.data_bgr = None

    async def recv(self):
        self.data_bgr = await self.camera_device.get_next_frame()
        frame = av.VideoFrame.from_ndarray(self.data_bgr, format='bgr24')
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame

class MediaStreamError(Exception):
    pass

async def index(request):
    content = open(os.path.join(ROOT, 'client/index.html'), 'r').read()
    return web.Response(content_type='text/html', text=content)

async def stylesheet(request):
    content = open(os.path.join(ROOT, 'client/style.css'), 'r').read()
    return web.Response(content_type='text/css', text=content)

async def javascript(request):
    content = open(os.path.join(ROOT, 'client/client.js'), 'r').read()
    return web.Response(content_type='application/javascript', text=content)

async def balena(request):
    content = open(os.path.join(ROOT, 'client/balena-cam.svg'), 'r').read()
    return web.Response(content_type='image/svg+xml', text=content)

async def balena_logo(request):
    content = open(os.path.join(ROOT, 'client/balena-logo.svg'), 'r').read()
    return web.Response(content_type='image/svg+xml', text=content)

async def favicon(request):
    return web.FileResponse(os.path.join(ROOT, 'client/favicon.png'))

async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(
        sdp=params['sdp'],
        type=params['type'])
    pc = pc_factory.create_peer_connection()
    pcs.add(pc)
    
    @pc.on('iceconnectionstatechange')
    async def on_iceconnectionstatechange():
        if pc.iceConnectionState == 'failed':
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer)

    for trans in pc.getTransceivers():
        if trans.kind == "audio" and camera_device.hasAudio:
            pc.addTrack(RTCAudioStream(camera_device))
        elif trans.kind == "video" and camera_device.video:
            pc.addTrack(RTCVideoStream(camera_device))

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))

async def mjpeg_handler(request):
    boundary = "frame"
    response = web.StreamResponse(status=200, reason='OK', headers={
        'Content-Type': 'multipart/x-mixed-replace; '
                        'boundary=%s' % boundary,
    })
    await response.prepare(request)
    while True:
        data = await camera_device.get_jpeg_frame()
        #await asyncio.sleep(0.5) # this means that the maximum FPS is 5
        await response.write(
            '--{}\r\n'.format(boundary).encode('utf-8'))
        await response.write(b'Content-Type: image/jpeg\r\n')
        await response.write('Content-Length: {}\r\n'.format(
                len(data)).encode('utf-8'))
        await response.write(b"\r\n")
        await response.write(data)
        await response.write(b"\r\n")
    return response

async def config(request):
    return web.Response(
        content_type='application/json',
        text=pc_factory.get_ice_config()
    )

async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)

def checkDeviceReadiness():
    if not os.path.exists(VIDEO_DEVICE) and platform.system() == 'Linux':
        print('Video device is not ready')
        print('Trying to load bcm2835-v4l2 driver...')
        os.system('bash -c "modprobe bcm2835-v4l2"')
        time.sleep(1)
        sys.exit()
    else:
        print('Video device is ready')

if __name__ == '__main__':
    if sys.platform != "win32" and MODE is 'dev':
        try:
            import ptvsd
            print("Enabling debugger")
            ptvsd.enable_attach(address=('100.0.0.104', 3000), redirect_output=True)
            ptvsd.wait_for_attach()
            print("Debugger attached")
        except Exception as ex:
            print("Debugger not attaching. %s", ex)

    checkDeviceReadiness()

    ROOT = os.path.dirname(__file__)
    pcs = set()
    camera_device = CameraDevice()

    flip = False
    try:
        if os.environ['rotation'] == '1':
            flip = True
    except:
        pass

    auth = []
    if 'username' in os.environ and 'password' in os.environ:
        print('\n#############################################################')
        print('Authorization is enabled.')
        print('Your balenaCam is password protected.')
        print('#############################################################\n')
        auth.append(BasicAuthMiddleware(username = os.environ['username'], password = os.environ['password']))
    else:
        print('\n#############################################################')
        print('Authorization is disabled.')
        print('Anyone can access your balenaCam, using the device\'s URL!')
        print('Set the username and password environment variables \nto enable authorization.')
        print('For more info visit: \nhttps://github.com/balena-io-playground/balena-cam')
        print('#############################################################\n')
    
    # Factory to create peerConnections depending on the iceServers set by user
    pc_factory = PeerConnectionFactory()

    app = web.Application(middlewares=auth)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get('/', index)
    app.router.add_get('/favicon.png', favicon)
    app.router.add_get('/balena-logo.svg', balena_logo)
    app.router.add_get('/balena-cam.svg', balena)
    app.router.add_get('/client.js', javascript)
    app.router.add_get('/style.css', stylesheet)
    app.router.add_post('/offer', offer)
    app.router.add_get('/mjpeg', mjpeg_handler)
    app.router.add_get('/ice-config', config)
    web.run_app(app, port=80)
