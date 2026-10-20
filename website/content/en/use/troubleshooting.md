---
title: Troubleshooting
group: Full Features Intro
order: 40
---
# Troubleshooting

First identify whether the browser has lost its Gateway connection, a compute device is offline, or an algorithm task has failed. A red state on the canvas is not a reason to keep submitting the same task.

## The interface will not open

Check that the startup terminal is still running and has not reported a port conflict. The default port is 8828; if you choose a different port, update the browser URL too.

Starting with `--host 127.0.0.1` only allows local access. Access from another computer also depends on the listening address, network connectivity, and port configuration.

## A Pack is missing or will not run

- Check the device in **Compute nodes**, then check whether the search in **Packs** is filtering it out.
- Check **Pack sources** in the device settings. These paths belong to the compute device.
- An invalid manifest may be skipped during scanning. Inspect the compute node logs and manifest rather than repeatedly refreshing the page.
- For `unassigned`, choose a compute device. For missing required inputs, connect the necessary ports.
- For an incompatible connection, check types, collection depth, and axis names. A valid connection does not guarantee that the data satisfies the algorithm's requirements.

## What task states mean

| State | What to do |
| --- | --- |
| Pending / assigned, waiting to start | The task has not started. Check the device and queue; do not submit it again. |
| Running | Inspect progress and logs. A task can still be running without a percentage indicator. |
| Stopped | A stop instruction was received; the task did not complete successfully. HoloLab does not record who issued the instruction. Run it again if needed. |
| Device disconnected | The task may still be running on the device. Check connectivity and wait for state synchronization to avoid duplicate work. |
| Interrupted, not recovered | No task was reclaimed after reconnecting, or recovery timed out. Check the device and any remaining processes before rerunning. |
| Failed | Read the failure in the logs, fix the cause, and try again. |

Failure reason `oom` means the executor reported insufficient memory or GPU memory. `user_error` points to parameters or inputs; `system_error` to the environment or system; `algo_error` to an abnormal algorithm exit. Consult the logs for the specific cause.

## Changed parameters, old results

The draft and submitted tasks are separate. Editing parameters neither updates an active process nor changes historical snapshots. Wait for edits to save, then run the steps that need updating from the draft.

If another page or client modified the workflow, preserve the edits you need and reload the latest version. Do not overwrite the other changes without reviewing them.

## Preview or file-location access fails

An artifact marked as cleaned up must be generated again. An offline device or files moved or deleted outside HoloLab can also break previews. Check that the device is online and the files remain at the recorded location.

Opening file locations requires Flops Cobrowser and a device mapping; ordinary browsers do not provide that entry. See [View results](results.md).
