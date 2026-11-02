from flask import Blueprint, request, jsonify
from database import db, Agenda

agenda_bp = Blueprint('agenda', __name__, url_prefix='/api/agenda')

@agenda_bp.route('/', methods=['GET'])
def get_agendas():
    agendas = Agenda.query.all()
    return jsonify([{'id': a.id, 'data': a.data} for a in agendas]), 200

@agenda_bp.route('/<int:id>', methods=['GET'])
def get_agenda(id):
    agenda = Agenda.query.get_or_404(id)
    return jsonify({'id': agenda.id, 'data': agenda.data}), 200

@agenda_bp.route('/', methods=['POST'])
def create_agenda():
    data = request.get_json()
    data_agenda = data['data']

    new_agenda = Agenda(data=data_agenda)
    db.session.add(new_agenda)
    db.session.commit()
    return jsonify({'id': new_agenda.id, 'data': new_agenda.data}), 201