import argparse
import asyncio
import threading
import os
import requests

from NovaApi.ListDevices.nbe_list_devices import request_device_list
from ProtoDecoders.decoder import parse_device_list_protobuf, get_canonic_ids, parse_device_update_protobuf
from NovaApi.ExecuteAction.LocateTracker.location_request import create_location_request
from NovaApi.nova_request import nova_request
from NovaApi.scopes import NOVA_ACTION_API_SCOPE
from NovaApi.util import generate_random_uuid
from Auth.fcm_receiver import FcmReceiver
from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import extract_locations
from datetime import datetime

API_TOKEN = None
PUSH_URL = None
periodic_jobs = {}
_fetch_location_lock = threading.Lock()

def _fetch_location(device_id, timeout=15):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = None
    request_uuid = generate_random_uuid()
    done = asyncio.Event()

    def handler(resp_hex):
        nonlocal result
        update = parse_device_update_protobuf(resp_hex)
        if update.fcmMetadata.requestUuid == request_uuid:
            result = update
            done.set()

    with _fetch_location_lock:
        fcm_token = FcmReceiver().register_for_location_updates(handler)

        try:
            payload = create_location_request(device_id, fcm_token, request_uuid)
            nova_request(NOVA_ACTION_API_SCOPE, payload)
            asyncio.get_event_loop().run_until_complete(asyncio.wait_for(done.wait(), timeout))
        finally:
            FcmReceiver().stop_listening()
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            asyncio.set_event_loop(None)

    return extract_locations(result) if result else []

def _get_latest_location(locations):
    with_coords = [l for l in locations if 'latitude' in l and 'longitude' in l]
    if not with_coords:
        return None
    return max(with_coords, key=lambda l: l.get('time', 0))

def _upload_location(device_id, location):
    if not PUSH_URL:
        raise RuntimeError('Push service URL not configured')
    if not location:
        raise RuntimeError('No valid location')
    
    params = {
        'id': DEVICE_MAPPINGS.get(device_id, device_id),
        'lat': location['latitude'],
        'lon': location['longitude'],
        'timestamp': int(location['time'] * 1000),  # Convert to milliseconds
        'accuracy': location.get('accuracy', 0),
        'altitude': location.get('altitude', 0),
    }
    
    try:
        requests.get(PUSH_URL, params=params, timeout=10, headers=CUSTOM_HEADERS)
    except Exception:
        pass

class PeriodicUploader:
    def __init__(self, device_id, interval):
        self.device_id = device_id
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread.join()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                locations = _fetch_location(self.device_id)
                location = _get_latest_location(locations)
                if location:
                    _upload_location(self.device_id, location)
            except Exception:
                pass
            if self._stop_event.wait(self.interval):
                break

def main():
    parser = argparse.ArgumentParser(description="Google Find Hub Sync")
    parser.add_argument('--auth-token', default=os.getenv('AUTH_TOKEN'))
    parser.add_argument('--push-url', default=os.getenv('PUSH_URL'))
    parser.add_argument('--headers' , default=os.getenv('CUSTOM_HEADERS'), help='Custom headers writtenen in "key1:value1,key2:value2" format')
    parser.add_argument('--device-mappings', default=os.getenv('DEVICE_MAPPINGS'), help='Transforms device IDs into custom ids "source1:target1,source2:target2" format')
    parser.add_argument('--interval', type=int, default=os.getenv('UPLOAD_INTERVAL', 300), help='Upload interval in seconds')
    args = parser.parse_args()

    if not args.auth_token:
        parser.error('argument --auth-token or AUTH_TOKEN environment variable is required')

    if not args.push_url:
        parser.error('argument --push-url or PUSH_URL environment variable is required')

    global API_TOKEN
    global PUSH_URL
    global CUSTOM_HEADERS
    global DEVICE_MAPPINGS
    API_TOKEN = args.auth_token
    PUSH_URL = args.push_url
    DEVICE_MAPPINGS = dict(mapping.split(':') for mapping in args.device_mappings.split(',')) if args.device_mappings else {}
    CUSTOM_HEADERS = dict(header.split(':') for header in args.headers.split(',')) if args.headers else {}

    print("Starting Google Find Hub Sync Microservice")

    # Add all device ids as jobs to restore periodic uploads
    device_list_resp = request_device_list()
    devices = parse_device_list_protobuf(device_list_resp)
    device_ids = get_canonic_ids(devices)

    for device in device_ids:
        print(f"Setting up periodic upload for device: {device}")
        if device not in periodic_jobs:
            job = PeriodicUploader(device[1], args.interval)
            periodic_jobs[device] = job
            job.start()

    try:
        while True:
            threading.Event().wait(timeout=1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        for job in periodic_jobs.values():
            job.stop()

if __name__ == '__main__':
    main()
