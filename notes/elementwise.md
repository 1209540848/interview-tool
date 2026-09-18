# CUDA Elementwise Add：普通版与 float4 向量化版

## 1. 普通 float 版本

```cpp
#include <cuda_runtime.h>

__global__ void elementwise_add_kernel(
    const float* __restrict__ A,
    const float* __restrict__ B,
    float* __restrict__ C,
    int N)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;

    if (idx < N) {
        C[idx] = A[idx] + B[idx];
    }
}

extern "C" void elementwise_add(
    const float* A,
    const float* B,
    float* C,
    int N)
{
    int threads = 256;
    int blocks = (N + threads - 1) / threads;

    elementwise_add_kernel<<<blocks, threads>>>(
        A, B, C, N
    );
}
```

普通版本的核心是：**一个线程处理一个 float**。连续线程访问连续地址，因此比较容易形成 coalesced memory access。

```text
Thread 0 → C[0] = A[0] + B[0]
Thread 1 → C[1] = A[1] + B[1]
Thread 2 → C[2] = A[2] + B[2]
...
```

---

## 2. float4 向量化版本

```cpp
#include <cuda_runtime.h>

__global__ void elementwise_add_float4_kernel(
    const float* __restrict__ A,
    const float* __restrict__ B,
    float* __restrict__ C,
    int N)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;

    int vecN = N / 4;

    if (idx < vecN) {

        const float4* A4 =
            reinterpret_cast<const float4*>(A);

        const float4* B4 =
            reinterpret_cast<const float4*>(B);

        float4* C4 =
            reinterpret_cast<float4*>(C);

        float4 a = A4[idx];
        float4 b = B4[idx];

        float4 c;

        c.x = a.x + b.x;
        c.y = a.y + b.y;
        c.z = a.z + b.z;
        c.w = a.w + b.w;

        C4[idx] = c;
    }

    int tailStart = vecN * 4;

    if (idx < N - tailStart) {
        int i = tailStart + idx;
        C[i] = A[i] + B[i];
    }
}

extern "C" void elementwise_add_float4(
    const float* A,
    const float* B,
    float* C,
    int N)
{
    int threads = 256;

    int vecN = N / 4;
    int tail = N % 4;

    int workItems = vecN > tail ? vecN : tail;

    int blocks =
        (workItems + threads - 1) / threads;

    elementwise_add_float4_kernel<<<blocks, threads>>>(
        A, B, C, N
    );
}
```
