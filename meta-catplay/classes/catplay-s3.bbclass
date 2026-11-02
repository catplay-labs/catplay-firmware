# SPDX-License-Identifier: MIT
# Credentials stay in BB_ORIGENV, not in recipe variables or generated config.

addhandler catplay_s3_validate
catplay_s3_validate[eventmask] = "bb.event.ConfigParsed bb.event.BuildStarted bb.event.BuildCompleted"

python catplay_s3_validate() {
    import catplay_s3.fetcher
    try:
        if isinstance(e, bb.event.ConfigParsed):
            catplay_s3.fetcher.validate(d)
        elif isinstance(e, bb.event.BuildStarted):
            catplay_s3.fetcher.begin_build(d)
        elif isinstance(e, bb.event.BuildCompleted):
            catplay_s3.fetcher.clear_indexes()
    except bb.fetch2.FetchError as error:
        bb.fatal(str(error))
}

BB_BASEHASH_IGNORE_VARS += "CATPLAY_S3_ENDPOINT CATPLAY_S3_REGION CATPLAY_S3_BUCKET CATPLAY_S3_AUTH_REVISION"
BB_BASEHASH_IGNORE_VARS += "CATPLAY_S3_TRACE_FILE"
