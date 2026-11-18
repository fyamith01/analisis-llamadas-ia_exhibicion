# 0006. Juez a ciegas y kappa de Cohen para medir el acuerdo entre modelos

**Estado:** aceptada · **Alcance:** sistema original y ejemplo ([`multiagente.py`](../../ejemplo/multiagente.py))

## Contexto

Para elegir el modelo de producción ([0003](0003-llm-local-con-api-como-referencia.md)) y vigilar su calidad hace falta una referencia. Si el revisor ve la respuesta del evaluador, tiende a confirmarla. Las categorías están desbalanceadas, así que el porcentaje de coincidencia exagera el acuerdo.

## Decisión

- Un juez etiqueta una muestra estratificada **sin ver** las predicciones del evaluador. En el ejemplo se garantiza por construcción: la función del juez solo recibe la transcripción.
- El acuerdo se mide con el kappa de Cohen, que descuenta el acuerdo esperado por azar.
- Un verificador determinista comprueba que la cita respalde la decisión.
- Los desacuerdos van a revisión humana.

## Consecuencias

- La comparación entre modelos se basa en una medida, no en la impresión de quien revisa.
- El verificador, al no ser un modelo, no puede equivocarse "con seguridad".
- Cuesta una segunda pasada del modelo sobre la muestra, y el juez también puede equivocarse: por eso los desacuerdos van a una persona y no se resuelven a favor de ninguno de los dos.

## Alternativas descartadas

- **Revisión humana de todo:** no escala al volumen mensual.
- **Juez que ve la respuesta del evaluador:** más barato de implementar, pero mide conformidad, no acuerdo.
- **Porcentaje de coincidencia:** con un motivo dominante, coincidir el 80 % puede ser peor que el azar.
