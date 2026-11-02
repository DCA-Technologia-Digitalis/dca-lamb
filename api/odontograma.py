from flask import Blueprint, request, jsonify
from database import db, Odontograma

odontograma_bp = Blueprint('odontograma', __name__, url_prefix='/api/odontograma')

@odontograma_bp.route('/', methods=['GET'])
def get_odontogramas():
    odontogramas = Odontograma.query.all()
    return jsonify([{'id': o.id, 'paciente_id': o.paciente_id} for o in odontogramas]), 200

@odontograma_bp.route('/<int:id>', methods=['GET'])
def get_odontograma(id):
    odontograma = Odontograma.query.get_or_404(id)
    return jsonify({'id': odontograma.id, 'paciente_id': odontograma.paciente_id}), 200

@odontograma_bp.route('/', methods=['POST'])
def create_odontograma():
    data = request.get_json()
    paciente_id = data['paciente_id']

    new_odontograma = Odontograma(paciente_id=paciente_id)
    db.session.add(new_odontograma)
    db.session.commit()
    return jsonify({'id': new_odontograma.id, 'paciente_id': new_odontograma.paciente_id}), 201