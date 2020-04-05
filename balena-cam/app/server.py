import asyncio, json, os, platform, sys, av, time
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration, VideoStreamTrack, AudioStreamTrack
from aiortc.contrib.media import MediaPlayer
from aiohttp_basicauth import BasicAuthMiddleware

class CameraDevice():
    def __init__(self):
        self.player = MediaPlayer('/dev/video1', format='v4l2', options={'video_size': '640x480'})
        
        if self.player.video is None:
            print("Failed to open video stream. Exiting...")
            sys.exit()

        if self.player.audio is None:
            print("Stream contains no audio.")

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
        self.kind = 'video'
        self._player = camera_device.player
        self._queue = asyncio.Queue()
        self._start = None

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError

        self._player._start(self)
        frame = await self._queue.get()
        if frame is None:
            self.stop()
            raise MediaStreamError
        return frame

    def stop(self):
        super().stop()
        if self._player is not None:
            self._player._stop(self)
            self._player = None

class RTCAudioStream(AudioStreamTrack):
    def __init__(self, camera_device):
        super().__init__()
        self.kind = 'audio'
        self._player = camera_device.player
        self._queue = asyncio.Queue()
        self._start = None

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError

        self._player._start(self)
        frame = await self._queue.get()
        if frame is None:
            self.stop()
            raise MediaStreamError
        return frame

    def stop(self):
        super().stop()
        if self._player is not None:
            self._player._stop(self)
            self._player = None

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
    
    # Add local media
    local_video = RTCVideoStream(camera_device)
    local_audio = RTCAudioStream(camera_device)
    pc.addTrack(local_video)
    pc.addTrack(local_audio)

    @pc.on('iceconnectionstatechange')
    async def on_iceconnectionstatechange():
        if pc.iceConnectionState == 'failed':
            await pc.close()
            pcs.discard(pc)
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))

# async def mjpeg_handler(request):
#     boundary = "frame"
#     response = web.StreamResponse(status=200, reason='OK', headers={
#         'Content-Type': 'multipart/x-mixed-replace; '
#                         'boundary=%s' % boundary,
#     })
#     await response.prepare(request)
#     while True:
#         data = await camera_device.get_jpeg_frame()
#         await asyncio.sleep(0.2) # this means that the maximum FPS is 5
#         await response.write(
#             '--{}\r\n'.format(boundary).encode('utf-8'))
#         await response.write(b'Content-Type: image/jpeg\r\n')
#         await response.write('Content-Length: {}\r\n'.format(
#                 len(data)).encode('utf-8'))
#         await response.write(b"\r\n")
#         await response.write(data)
#         await response.write(b"\r\n")
#     return response

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
    if not os.path.exists('/dev/video1') and platform.system() == 'Linux':
        print('Video device is not ready')
        print('Trying to load bcm2835-v4l2 driver...')
        os.system('bash -c "modprobe bcm2835-v4l2"')
        time.sleep(1)
        sys.exit()
    else:
        print('Video device is ready')

if __name__ == '__main__':
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
    #app.router.add_get('/mjpeg', mjpeg_handler)
    app.router.add_get('/ice-config', config)
    web.run_app(app, port=80)
