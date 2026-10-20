"""Check the staged Git tree for local artifacts, secrets and non-LFS binaries."""
from pathlib import PurePosixPath
import re
import subprocess


def main():
    names=subprocess.check_output(['git','ls-files','-z']).decode().split('\0')
    errors=[]
    for name in filter(None,names):
        path=PurePosixPath(name)
        if path.parts[0] in {'runs','work','logs','vendor','models','data','inputs','outputs','artifacts'}:
            errors.append('Local artifact directory: '+name)
        if re.search(r'\.local\.(?:json|toml|ya?ml)$',name) or path.name.startswith('.env'):
            errors.append('Local configuration: '+name)
        data=subprocess.check_output(['git','show',':'+name])
        if len(data)>2*1024**2:
            errors.append('Large file in ordinary Git: '+name)
        if name.endswith('.safetensors') and not data.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            errors.append('Weight is not an LFS pointer: '+name)
        if re.search(rb'(?m)^-----BEGIN (?:OPENSSH|RSA) PRIVATE KEY-----$',data):
            errors.append('Private key: '+name)
        if re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})',data):
            errors.append('GitHub credential: '+name)
        if ('/'+'root/autodl-tmp').encode() in data or ('/'+'root/Documents').encode() in data:
            errors.append('Machine-specific workspace path: '+name)
    if errors:raise SystemExit('\n'.join(errors))
    print('Archive index checks passed.')


if __name__=='__main__':main()
