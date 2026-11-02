# SPDX-License-Identifier: MIT
# Override OE-Core pstaging_fetch only to fetch sstate companions concurrently.
inherit sstate

def pstaging_fetch(sstatefetch, d):
    import bb.fetch2
    from concurrent.futures import ThreadPoolExecutor

    # Only try and fetch if the user has configured a mirror
    mirrors = d.getVar('SSTATE_MIRRORS')
    if not mirrors:
        return

    # Copy the data object and override DL_DIR and SRC_URI
    localdata = bb.data.createCopy(d)

    dldir = localdata.expand("${SSTATE_DIR}")

    localdata.delVar('MIRRORS')
    localdata.setVar('FILESPATH', dldir)
    localdata.setVar('DL_DIR', dldir)
    localdata.setVar('PREMIRRORS', mirrors)

    # if BB_NO_NETWORK is set but we also have SSTATE_MIRROR_ALLOW_NETWORK,
    # we'll want to allow network access for the current set of fetches.
    if bb.utils.to_boolean(localdata.getVar('BB_NO_NETWORK')) and \
            bb.utils.to_boolean(localdata.getVar('SSTATE_MIRROR_ALLOW_NETWORK')):
        localdata.delVar('BB_NO_NETWORK')

    # Try a fetch from the sstate mirror, if it fails just return and
    # we will build the package
    uris = ['file://{0};downloadfilename={0}'.format(sstatefetch),
            'file://{0}.siginfo;downloadfilename={0}.siginfo'.format(sstatefetch)]
    if bb.utils.to_boolean(d.getVar("SSTATE_VERIFY_SIG"), False):
        uris += ['file://{0}.sig;downloadfilename={0}.sig'.format(sstatefetch)]

    with bb.utils.umask(bb.utils.to_filemode(d.getVar("OE_SHARED_UMASK"))):
        bb.utils.mkdirhier(dldir)

        def fetch_one(srcuri):
            taskdata = bb.data.createCopy(localdata)
            taskdata.delVar('SRC_URI')
            taskdata.setVar('SRC_URI', srcuri)
            try:
                fetcher = bb.fetch2.Fetch([srcuri], taskdata, cache=False)
                fetcher.checkstatus()
                fetcher.download()

            except bb.fetch2.BBFetchException:
                pass

        with ThreadPoolExecutor(max_workers=len(uris)) as pool:
            list(pool.map(fetch_one, uris))
