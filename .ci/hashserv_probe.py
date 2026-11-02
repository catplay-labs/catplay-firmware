"""Read/write probe using the same hashserv client as BitBake, inside Docker."""
import argparse
import hashlib
import sys
from contextlib import closing

sys.path.insert(0, '/usr/lib/bitbake/lib')
import hashserv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('endpoint')
    parser.add_argument('--smoke')
    parser.add_argument('--identity', default='health')
    parser.add_argument('--expect-identity', action='append', default=[])
    args = parser.parse_args()
    with closing(hashserv.create_client(args.endpoint)) as client:
        if args.smoke:
            digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
            method = 'catplay-ci-smoke-' + args.smoke
            task = digest(args.smoke + args.identity)
            canonical = digest(args.smoke + '-canonical')
            client.report_unihash(task, method, digest(args.smoke + '-output'), canonical)
            assert client.get_unihash(method, task) == canonical
            for identity in args.expect_identity:
                assert client.get_unihash(method, digest(args.smoke + identity)) == canonical
            print('hashserv: report/read passed for ' + args.identity, flush=True)
        else:
            client.get_unihash('catplay-ci-health', '0' * 64)


if __name__ == '__main__':
    main()
