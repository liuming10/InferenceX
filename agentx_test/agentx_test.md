# 启动命令
```bash
aiperf profile \
  --scenario inferencex-agentx-mvp \
  --url http://127.0.0.1:8077 \
  --endpoint /v1/chat/completions \
  --endpoint-type chat \
  --streaming \
  --ui simple \
  --model qwen3.6 \
  --tokenizer /models/Eco-Tech/Qwen3.6-35B-A3B-w8a8 \
  --public-dataset semianalysis_cc_traces_weka_062126_256k \
  --max-context-length 262144 \
  --concurrency 16 \
  --benchmark-duration 1800 \
  --random-seed 42 \
  --stats-interval 30 \
  --failed-request-threshold 0.10 \
  --trajectory-start-min-ratio 0.25 \
  --trajectory-start-max-ratio 0.75 \
  --warmup-requests-per-lane 10 \
  --trace-idle-gap-cap-seconds 300 \
  --warmup-grace-period 1800 \
  --use-server-token-count
```

```bash
export HF_ENDPOINT=https://hf-mirror.com
/stortest/lium_space/agentX/agentx-harness/.venv312/bin/aiperf profile \
    --scenario inferencex-agentx-mvp \
    --url http://127.0.0.1:30001 \
    --endpoint /v1/chat/completions \
    --endpoint-type chat \
    --streaming \
    --model Qwen3.5 \
    --tokenizer /ai_data/models/Qwen3.5-35B-A3B \
    --concurrency 16 \
    --benchmark-duration 3600 \
    --stats-interval 30 \
    --random-seed 42 \
    --max-context-length 262144 \
    --failed-request-threshold 0.10 \
    --trajectory-start-min-ratio 0.25 \
    --trajectory-start-max-ratio 0.75 \
    --warmup-requests-per-lane 10 \
    --trace-idle-gap-cap-seconds 300 \
    --warmup-grace-period 1800 \
    --use-server-token-count \
    --no-gpu-telemetry \
    --tokenizer-trust-remote-code \
    --num-dataset-entries 393 \
    --slice-duration 1.0 \
    --output-artifact-dir ./results/aiperf_artifacts \
    --public-dataset semianalysis_cc_traces_weka_062126_256k
```




```bash
aiperf profile \
    --scenario inferencex-agentx-mvp \
    --url http://127.0.0.1:30001 \
    --endpoint /v1/chat/completions \
    --endpoint-type chat \
    --streaming \
    --model Qwen3.5 \
    --tokenizer /ai_data/models/Qwen3.5-35B-A3B \
    --concurrency 16 \
    --benchmark-duration 3600 \
    --stats-interval 30 \
    --random-seed 42 \
    --failed-request-threshold 0.10 \
    --trajectory-start-min-ratio 0.25 \
    --trajectory-start-max-ratio 0.75 \
    --warmup-requests-per-lane 10 \
    --trace-idle-gap-cap-seconds 300 \
    --warmup-grace-period 1800 \
    --use-server-token-count \
    --no-gpu-telemetry \
    --tokenizer-trust-remote-code \
    --max-context-length 262144\
    --num-dataset-entries 393 \
    --slice-duration 1.0 \
    --output-artifact-dir ./aiperf_artifacts \
    --public-dataset semianalysis_cc_traces_weka_062126_256k
```