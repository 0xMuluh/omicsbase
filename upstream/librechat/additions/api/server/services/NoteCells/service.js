const mongoose = require('mongoose');
const { createNoteService } = require('@librechat/api');
const models = require('./models');
const { executeOnEngine, fetchArtifactFromEngine, signalCancel, INTERNAL_SECRET } = require('./engineClient');
const { bridgeConversationFiles } = require('./noteDataBridge');

module.exports = createNoteService({
  models,
  getConversationModel: () => mongoose.models.Conversation,
  executeOnEngine,
  fetchArtifactFromEngine,
  signalCancel,
  internalSecret: INTERNAL_SECRET,
  bridgeConversationFiles,
});
