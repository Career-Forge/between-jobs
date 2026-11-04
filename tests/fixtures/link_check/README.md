# Link-check fixtures

Small, trimmed copies of real answers from employers' public job-board endpoints, captured on
2026-11-04. They back `tests/test_link_check.py` and `tests/test_link_check_fetch.py`: the decisions
in `src/between_jobs/api/link_check.py` are tested against the shapes these endpoints actually
return, not against shapes written from memory.

Every file is one JSON object: `captured` (the date), `request` (what was asked), `status`, and the
`body` as the server sent it, or a trimmed copy of it (below). Nothing is kept of any job description or
employer text beyond ids, urls the API itself returned and, in two files, a posting's id-bearing path.

## How they were captured

Twelve requests in all, one at a time, two seconds apart, from one machine, with the User-Agent the
feature sends and no redirects followed. Only these public, documented endpoints were called. The
commands below are the ones that were run, written without the flags that also dumped the response
headers to a file.
`robots.txt` was not re-read for this capture (it is not one of the endpoints it was limited to):
an earlier read of the same hosts, checking these same paths against their robots.txt the same
day, found nothing disallowed.

```bash
UA='BetweenJobsLinkCheck/1.0 (+https://between-jobs.tech; support@between-jobs.tech)'
get() { curl -sS --max-time 15 --max-filesize 16777216 -A "$UA" -H 'Accept: application/json' -o "$1.body" "$2"; sleep 2; }

get gh_list     'https://boards-api.greenhouse.io/v1/boards/stripe/jobs'
get gh_job_404  'https://boards-api.greenhouse.io/v1/boards/stripe/jobs/1'
get gh_board_404 'https://boards-api.greenhouse.io/v1/boards/no-such-board-0000/jobs'
get lever_list  'https://api.lever.co/v0/postings/leverdemo?mode=json'
get lever_404   'https://api.lever.co/v0/postings/leverdemo/00000000-0000-0000-0000-000000000000?mode=json'
get ashby_list  'https://api.ashbyhq.com/posting-api/job-board/ashby'
curl -sS --max-time 15 -A "$UA" -H 'Accept: text/html' -o ashby_hosted_404.body \
  -w '%{http_code}\n' 'https://jobs.ashbyhq.com/ashby/00000000-0000-0000-0000-000000000000'
get workday_ntt_s22    'https://nttlimited.wd3.myworkdayjobs.com/wday/cxs/nttlimited/NTT_Careers/job/Bangalore-India/AI-Engineer_R-151594'
get workday_nvidia_200 'https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Solutions-Architect--AI-Hyperscalers_JR2017895'
get workday_nvidia_404 'https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/Nowhere/Nothing-To-See_JR0000000'
curl -sS --max-time 15 -A "$UA" -H 'Content-Type: application/json' -X POST \
  --data '{"appliedFacets":{},"limit":5,"offset":0,"searchText":"R-151594"}' \
  -o workday_ntt_search.body 'https://nttlimited.wd3.myworkdayjobs.com/wday/cxs/nttlimited/NTT_Careers/jobs'
sleep 2
curl -sS --max-time 15 -A "$UA" -H 'Content-Type: application/json' -X POST \
  --data '{"appliedFacets":{},"limit":5,"offset":0,"searchText":"JR2017895"}' \
  -o workday_nvidia_search.body 'https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs'
```

The NTT posting is a real posting that has since closed (the tenant answers its detail with a 403
and error code S22, and its tenant search no longer lists it). The NVIDIA posting was open when it
was captured. The never-existing ids are zero UUIDs and `JR0000000`.

## How each file was trimmed

| File | Status | Kept from the raw answer |
|---|---|---|
| `greenhouse_board_list.json` | 200 | the first 3 jobs, `id` and `absolute_url` only; `meta.total` rewritten from 754 to 3 to match |
| `greenhouse_board_404.json` | 404 | verbatim |
| `greenhouse_job_404.json` | 404 | verbatim; byte-identical to the board 404, which is why a job 404 alone never counts |
| `lever_postings_list.json` | 200 | the first 2 postings, `id` and `text` only |
| `lever_posting_404.json` | 404 | verbatim |
| `ashby_job_board.json` | 200 | `apiVersion` and the first 3 jobs, `id` and `isListed` only |
| `ashby_hosted_404.json` | 404 | the status, the content type and one marker string from the page (`"posting":null`); the page itself is not kept |
| `workday_detail_s22_403.json` | 403 | verbatim |
| `workday_detail_404.json` | 404 | verbatim |
| `workday_detail_200.json` | 200 | `jobPostingInfo.id`, `.jobReqId` and `.canApply` only |
| `workday_search_no_match.json` | 200 | verbatim (the tenant search is up and has no match) |
| `workday_search_match.json` | 200 | `total`, and for the one posting its `externalPath` and `bulletFields` |

The trimming was done with `jq`, for example:

```bash
jq '{captured: "2026-11-04", request: "GET https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
     status: 200, body: {jobs: [.jobs[:3][] | {id, absolute_url}], meta: {total: 3}}}' gh_list.body
jq '{captured: "2026-11-04", request: "...", status: 200,
     body: {jobPostingInfo: {id: .jobPostingInfo.id, jobReqId: .jobPostingInfo.jobReqId,
                             canApply: .jobPostingInfo.canApply}}}' workday_nvidia_200.body
```

## What is NOT real, and where the tests say so

- No real `isListed: false` Ashby posting and no real Workday detail with `canApply: false` was
  seen: every posting in the captures was listed and applyable. The tests that need them take the
  real fixture and change that one field, and say so where they do it.
- No real Lever answer from the EU region (`api.eu.lever.co`) was captured: the EU tests reuse the
  global fixtures, answered from the EU host by a stub.
- A real bare Workday 403 (one that is not S22) was not seen either; that test uses a hand-written
  body, and says so.
- Every capture succeeded; nothing in this directory was written by hand.
