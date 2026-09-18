# CUDA Reduce Sum 两种实现

## 1. Shared Memory Reduce

思路：每个线程先通过 Grid-Stride Loop 累加自己的局部结果，再把结果写入 Shared Memory，随后在 Block 内不断对半做归约，最后由线程 0 使用 `atomicAdd` 把当前 Block 的结果累加到全局输出。

```cpp
#include <cuda_runtime.h>

__global__ void reduce_sum_kernel(
    const float* __restrict__ input,
    float* output,
    int N)
{
    extern __shared__ float shared[];

    int tid = threadIdx.x;
    int idx = blockIdx.x * blockDim.x + threadIdx.x;

    float sum = 0.0f;

    for (int i = idx; i < N; i += blockDim.x * gridDim.x) {
        sum += input[i];
    }

    shared[tid] = sum;
    __syncthreads();

    for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
        if (tid < stride) {
            shared[tid] += shared[tid + stride];
        }
        __syncthreads();
    }

    if (tid == 0) {
        atomicAdd(output, shared[0]);
    }
}

extern "C" float reduce_sum(
    const float* input,
    int N)
{
    float* d_output;

    cudaMalloc(&d_output, sizeof(float));
    cudaMemset(d_output, 0, sizeof(float));

    int threads = 256;
    int blocks = (N + threads - 1) / threads;
    blocks = blocks > 1024 ? 1024 : blocks;

    reduce_sum_kernel<<<
        blocks,
        threads,
        threads * sizeof(float)
    >>>(
        input,
        d_output,
        N
    );

    float result;

    cudaMemcpy(
        &result,
        d_output,
        sizeof(float),
        cudaMemcpyDeviceToHost
    );

    cudaFree(d_output);

    return result;
}
```

---

## 2. Warp Shuffle 两级 Reduce

思路：先在每个 Warp 内使用 `__shfl_xor_sync` 完成第一次 Reduce，每个 Warp 的 lane 0 把结果写入 Shared Memory；然后由第 0 个 Warp 读取所有 Warp 的部分和，再进行第二次 Warp Shuffle Reduce，最终得到当前 Block 的结果。

```cpp
#include <cuda_runtime.h>

#define WARP_SIZE 32

// Warp 内 Reduce Sum
template <int kWarpSize = WARP_SIZE>
__device__ __forceinline__
float warp_reduce_sum(float val)
{
    #pragma unroll
    for (int mask = kWarpSize >> 1; mask >= 1; mask >>= 1) {
        val += __shfl_xor_sync(0xffffffff, val, mask);
    }

    return val;
}


template <int NUM_THREADS = 256>
__global__ void reduce_sum_kernel(
    const float* __restrict__ input,
    float* output,
    int N)
{
    int tid = threadIdx.x;
    int idx = blockIdx.x * NUM_THREADS + tid;

    constexpr int NUM_WARPS =
        (NUM_THREADS + WARP_SIZE - 1) / WARP_SIZE;

    __shared__ float warp_sum[NUM_WARPS];

    float sum = (idx < N) ? input[idx] : 0.0f;

    int warp = tid / WARP_SIZE;
    int lane = tid % WARP_SIZE;


    // ==========================================
    // 第一次 Warp Shuffle
    // 每个 Warp 内部进行 Reduce
    // ==========================================

    sum = warp_reduce_sum<WARP_SIZE>(sum);

    // 每个 Warp 的 lane 0 保存 Warp Sum
    if (lane == 0) {
        warp_sum[warp] = sum;
    }

    __syncthreads();


    // ==========================================
    // 第二次 Warp Shuffle
    // 第 0 个 Warp 对所有 Warp Sum 再做 Reduce
    // ==========================================

    sum = (lane < NUM_WARPS)
        ? warp_sum[lane]
        : 0.0f;

    if (warp == 0) {
        sum = warp_reduce_sum<NUM_WARPS>(sum);
    }


    // 一个 Block 最终得到一个结果
    if (tid == 0) {
        atomicAdd(output, sum);
    }
}
```
