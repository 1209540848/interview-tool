# CUDA SGEMM：Shared Memory + Register Tiling

## 参数配置

```cpp
#define BM 128
#define BN 128
#define BK 8

#define TM 8
#define TN 8
```

- `BM = 128`：一个 Block 负责计算 C 的 128 行。
- `BN = 128`：一个 Block 负责计算 C 的 128 列。
- `BK = 8`：K 维每次加载 8 个元素。
- `TM = 8`：一个 Thread 负责计算 8 行结果。
- `TN = 8`：一个 Thread 负责计算 8 列结果。
- 每个线程最终计算一个 `8 × 8` 的输出 tile。
- 每个 Block 使用 `16 × 16 = 256` 个线程，计算一个 `128 × 128` 的 C tile。

---

## 完整代码

```cpp
#include <cuda_runtime.h>

#define BM 128
#define BN 128
#define BK 8

#define TM 8
#define TN 8

__global__ void matrix_multiplication_kernel(
    const float* __restrict__ A,
    const float* __restrict__ B,
    float* __restrict__ C,
    int M,
    int N,
    int K)
{
    int tx = threadIdx.x;
    int ty = threadIdx.y;

    int tid = ty * blockDim.x + tx;
    int numThreads = blockDim.x * blockDim.y;

    int blockRow = blockIdx.y * BM;
    int blockCol = blockIdx.x * BN;

    __shared__ float As[BM][BK];
    __shared__ float Bs[BK][BN];

    float accum[TM][TN] = {0.0f};
    float regA[TM];
    float regB[TN];

    int threadRow = ty * TM;
    int threadCol = tx * TN;

    for (int k0 = 0; k0 < K; k0 += BK) {

        // Cooperative load: A tile -> Shared Memory
        for (int idx = tid; idx < BM * BK; idx += numThreads) {

            int row = idx / BK;
            int col = idx % BK;

            int globalRow = blockRow + row;
            int globalCol = k0 + col;

            if (globalRow < M && globalCol < K) {
                As[row][col] = A[globalRow * K + globalCol];
            } else {
                As[row][col] = 0.0f;
            }
        }

        // Cooperative load: B tile -> Shared Memory
        for (int idx = tid; idx < BK * BN; idx += numThreads) {

            int row = idx / BN;
            int col = idx % BN;

            int globalRow = k0 + row;
            int globalCol = blockCol + col;

            if (globalRow < K && globalCol < N) {
                Bs[row][col] = B[globalRow * N + globalCol];
            } else {
                Bs[row][col] = 0.0f;
            }
        }

        __syncthreads();

        #pragma unroll
        for (int k = 0; k < BK; ++k) {

            #pragma unroll
            for (int i = 0; i < TM; ++i) {
                regA[i] = As[threadRow + i][k];
            }

            #pragma unroll
            for (int j = 0; j < TN; ++j) {
                regB[j] = Bs[k][threadCol + j];
            }

            #pragma unroll
            for (int i = 0; i < TM; ++i) {

                #pragma unroll
                for (int j = 0; j < TN; ++j) {
                    accum[i][j] += regA[i] * regB[j];
                }
            }
        }

        __syncthreads();
    }

    #pragma unroll
    for (int i = 0; i < TM; ++i) {

        int row = blockRow + threadRow + i;

        #pragma unroll
        for (int j = 0; j < TN; ++j) {

            int col = blockCol + threadCol + j;

            if (row < M && col < N) {
                C[row * N + col] = accum[i][j];
            }
        }
    }
}

extern "C" void solve(
    const float* A,
    const float* B,
    float* C,
    int M,
    int N,
    int K)
{
    dim3 threadsPerBlock(
        BN / TN,
        BM / TM
    );

    dim3 blocksPerGrid(
        (N + BN - 1) / BN,
        (M + BM - 1) / BM
    );

    matrix_multiplication_kernel<<<blocksPerGrid, threadsPerBlock>>>(
        A,
        B,
        C,
        M,
        N,
        K
    );

    cudaDeviceSynchronize();
}
```

---

## 核心结构速记

这份 SGEMM 使用两层 tiling：一个 Block 负责计算 `128 × 128` 的输出块，并沿 K 维以 `BK = 8` 为步长把 A 的 `128 × 8` tile 和 B 的 `8 × 128` tile 协作加载到 Shared Memory；Block 内共有 `16 × 16 = 256` 个线程，每个线程负责一个 `8 × 8` 的输出 tile。计算时先把 Shared Memory 中需要的 A、B 数据加载到 `regA` 和 `regB`，再通过外积累加到 `accum[8][8]`，最终写回 Global Memory。
