"""Read portal-drive metadata, optionally verify one resumable test upload.

Run in backend: ./venv/bin/python -m maintenance.check_portal_storage
Explicit write test: append --write-test. Creates a UUID-named text file in
ArckenitesPortalFiles/Diagnostics, verifies it using the production upload
client, and deletes only that file. Does not enable any portal feature flag.
Never prints Microsoft tokens or secrets.
"""
import argparse
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote
import httpx
from app.config import settings
from app.core.onedrive_store import GRAPH, OneDriveStore
from app.core.portal_storage import ensure_folder


def main(argv=None):
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument('--write-test',action='store_true')
    opts=args.parse_args(argv)
    print('Portal document uploads enabled:',settings.portal_onedrive_enabled)
    print('Recording uploads enabled:',settings.onedrive_upload_enabled)
    print('Recording capture enabled:',getattr(settings, 'recording_enabled', False))
    if not settings.ms_graph_configured or not settings.onedrive_drive_id:
        print('Microsoft connection or destination drive is missing.')
        return 2
    store=OneDriveStore(folder=settings.portal_onedrive_folder_path.rstrip('/')+'/Diagnostics',enabled=True)
    item=None
    client=store._client()
    name='portal-test-'+uuid.uuid4().hex+'.txt'
    try:
        url=f'{GRAPH}/drives/{quote(store.drive_id,safe="")}'
        response=client.get(url,params={'$select':'id,name,driveType'},headers=store._bearer())
        if response.status_code!=200:
            print('Drive access failed. HTTP status:',response.status_code)
            return 1
        print('Drive access OK:',response.json().get('name'))
        if opts.write_test:
            ensure_folder(store)
            with tempfile.TemporaryDirectory(prefix='arck-storage-check-') as tmp:
                path=Path(tmp)/name;path.write_bytes(b'Arckenites private portal storage verification\n')
                item=store.upload_file(path,name)
            print('Resumable upload verified by size and QuickXorHash.')
            content=client.get(f'{url}/items/{quote(item.item_id,safe="")}/content',
                               headers=store._bearer(),follow_redirects=True)
            if content.status_code!=200 or content.content!=b'Arckenites private portal storage verification\n':
                print('Download verification failed.')
                return 1
            print('Download verification passed.')
        return 0
    except Exception as exc:
        print('Storage check failed. Error type:',type(exc).__name__)
        return 1
    finally:
        # The unique pathname is owned by this invocation even if final hash
        # verification failed after the object was uploaded.
        if opts.write_test:
            target=f'{GRAPH}/drives/{quote(store.drive_id,safe="")}/items/{quote(item.item_id,safe="")}' if item else store._item_path(name).removesuffix(':')
            try:
                result=client.delete(target,headers=store._bearer())
                if result.status_code in (204,404):print('Test file cleanup complete.')
                else:print('Test file cleanup needs attention. HTTP status:',result.status_code,'Filename:',name)
            except Exception as exc:
                print('Test file cleanup needs attention. Error type:',type(exc).__name__,'Filename:',name)
        client.close()


if __name__=='__main__':
    raise SystemExit(main())
