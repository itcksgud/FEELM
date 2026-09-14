FROM apache/spark:4.1.3-scala2.13-java21-python3-ubuntu
USER root
RUN pip3 install --no-cache-dir --target=/opt/feelm-python numpy==1.26.4 scipy==1.15.2 pandas==2.2.3 pyarrow==19.0.1 psutil==7.0.0
ENV PYTHONPATH=/opt/feelm-python
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
USER 185
