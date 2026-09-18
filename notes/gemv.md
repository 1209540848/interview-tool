# CUDA SGEMV：基础版与 Warp 优化版

## 1. 基础版本：一个线程计算一行

思路：一个线程负责矩阵的一整行，顺序计算这一行和向量 `x` 的点积，最后写入 `y[row]`。

```cpp
#include <cuda_runtime.h>

__global__ void sgemv_basic_kernel(
    const float* __restrict__ A,
    const float* __restrict__ x,
    float* __restrict__ y,
    int M,
    int N)
{
    int row = blockIdx.x * blockDim.x + threadIdx.x;

    if (row < M) {

        float sum = 0.0f;

        for (int col = 0; col < N; ++col) {
            sum += A[row * N + col] * x[col];
        }

        y[row] = sum;
    }
}

extern "C" void sgemv_basic(
    const float* A,
    const float* x,
    float* y,
    int M,
    int N)
{
    int threads = 256;
    int blocks = (M + threads - 1) / threads;

    sgemv_basic_kernel<<<blocks, threads>>>(
        A,
        x,
        y,
        M,
        N
    );
}
```

---

## 2. 优化版本：一个 Warp 计算一行 + Warp Shuffle

思路：一个 Warp 的 32 个线程共同计算一行，每个 lane 负责这一行的一部分列，先分别得到 partial sum，再通过 `__shfl_down_sync` 在 Warp 内完成 Reduce，最后由 lane 0 写出这一行结果。

```cpp
#include <cuda_runtime.h>

#define WARP_SIZE 32

__device__ __forceinline__
float warp_reduce_sum(float val)
{
    #pragma unroll
    for (int offset = WARP_SIZE / 2;
         offset > 0;
         offset >>= 1)
    {
        val += __shfl_down_sync(
            0xffffffff,
            val,
            offset
        );
    }

    return val;
}

template <int WARPS_PER_BLOCK = 8>
__global__ void sgemv_warp_kernel(
    const float* __restrict__ A,
    const float* __restrict__ x,
    float* __restrict__ y,
    int M,
    int N)
{
    int tid = threadIdx.x;

    int warp_id = tid / WARP_SIZE;
    int lane = tid % WARP_SIZE;

    int row =
        blockIdx.x * WARPS_PER_BLOCK + warp_id;

    if (row >= M)
        return;

    float sum = 0.0f;

    for (int col = lane; col < N; col += WARP_SIZE) {
        sum += A[row * N + col] * x[col];
    }

    sum = warp_reduce_sum(sum);

    if (lane == 0) {
        y[row] = sum;
    }
}

extern "C" void sgemv_warp(
    const float* A,
    const float* x,
    float* y,
    int M,
    int N)
{
    constexpr int WARPS_PER_BLOCK = 8;

    int threads =
        WARPS_PER_BLOCK * WARP_SIZE;

    int blocks =
        (M + WARPS_PER_BLOCK - 1)
        / WARPS_PER_BLOCK;

    sgemv_warp_kernel<WARPS_PER_BLOCK>
        <<<blocks, threads>>>(
            A,
            x,
            y,
            M,
            N
        );
}
```

---

## 3. 核心区别

```text
基础版：
1 Thread → 1 Row → 顺序完成 N 次乘加

优化版：
1 Warp → 1 Row
32 个 lane 分摊 N 个元素
→ partial sum
→ Warp Shuffle Reduce
→ lane 0 写结果
```

优化版的主要优点是：

```text
提高一行内部的并行度
+
连续 lane 访问连续 A 元素
+
更容易形成 coalesced memory access
+
使用 Warp Shuffle 完成 Reduce
```
