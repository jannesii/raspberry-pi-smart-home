from ._controller import (
    ACMixin,
    AuthMixin,
    CarHeaterKFactorMixin,
    CarHeaterMixin,
    CarHeaterReadyByMixin,
    CarHeaterSensorMixin,
    ControllerBase,
    LoggingControlMixin,
    LogsMixin,
    MedicineCalculatorMixin,
    MigrationMixin,
    SensorsMixin,
    ThreeDMixin,
    YnabCategorizerMixin,
)


class Controller(
    ControllerBase,
    ACMixin,
    AuthMixin,
    CarHeaterMixin,
    CarHeaterKFactorMixin,
    CarHeaterReadyByMixin,
    CarHeaterSensorMixin,
    LoggingControlMixin,
    LogsMixin,
    MedicineCalculatorMixin,
    MigrationMixin,
    SensorsMixin,
    ThreeDMixin,
    YnabCategorizerMixin,
):
    pass
